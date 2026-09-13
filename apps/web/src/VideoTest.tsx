import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowDownToLine,
  Film,
  Play,
  ShieldCheck,
  Square,
  X,
} from "lucide-react";
import {
  VIDEO_LIMITS,
  changedPixelRatio,
  groupActivitySamples,
  rgbaToLuminance,
  validateVideoFile,
  validateVideoMetadata,
} from "./videoActivity";
import type { ActivitySample, ActivitySegment } from "./videoActivity";
import "./videoTest.css";

type Selection = { url: string; name: string; bytes: number };
type Metadata = { duration: number; width: number; height: number };
type TestState =
  | "empty"
  | "loading"
  | "ready"
  | "analysing"
  | "complete"
  | "cancelled"
  | "error";
const DECODE_ERROR =
  "This video could not be decoded. Try an MP4 (H.264) or WebM file supported by this browser.";
const ANALYSIS_TIMEOUT_MS = 120_000;

function timestamp(seconds: number) {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}
function releaseVideo(video: HTMLVideoElement | null) {
  if (!video) return;
  video.pause();
  video.removeAttribute("src");
  video.load();
}
function mediaStep(
  video: HTMLVideoElement,
  event: "loadeddata" | "seeked",
  signal: AbortSignal,
  action: () => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Analysis cancelled", "AbortError"));
      return;
    }
    const cleanup = () => {
      clearTimeout(timer);
      video.removeEventListener(event, done);
      video.removeEventListener("error", error);
      signal.removeEventListener("abort", abort);
    };
    const done = () => {
      cleanup();
      resolve();
    };
    const error = () => {
      cleanup();
      reject(new Error(DECODE_ERROR));
    };
    const abort = () => {
      cleanup();
      reject(new DOMException("Analysis cancelled", "AbortError"));
    };
    const timer = setTimeout(() => {
      cleanup();
      reject(
        new Error(
          "Video decoding took too long. Try a shorter clip or a different supported encoding.",
        ),
      );
    }, 8_000);
    video.addEventListener(event, done, { once: true });
    video.addEventListener("error", error, { once: true });
    signal.addEventListener("abort", abort, { once: true });
    try {
      action();
    } catch {
      error();
    }
  });
}

export default function VideoTest({ branchName }: { branchName: string }) {
  const [selection, setSelection] = useState<Selection | null>(null);
  const [metadata, setMetadata] = useState<Metadata | null>(null);
  const [state, setState] = useState<TestState>("empty");
  const [error, setError] = useState("");
  const [progress, setProgress] = useState(0);
  const [sampleCount, setSampleCount] = useState(0);
  const [sampledThrough, setSampledThrough] = useState(0);
  const [segments, setSegments] = useState<ActivitySegment[]>([]);
  const [playhead, setPlayhead] = useState(0);
  const [dragging, setDragging] = useState(false);
  const selectionRef = useRef<Selection | null>(null);
  const previewRef = useRef<HTMLVideoElement>(null);
  const decoderRef = useRef<HTMLVideoElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const running = state === "analysing";
  const bindPreview = useCallback((video: HTMLVideoElement | null) => {
    if (previewRef.current !== video) releaseVideo(previewRef.current);
    previewRef.current = video;
  }, []);

  useEffect(() => {
    const visibility = () => {
      if (document.hidden) abortRef.current?.abort();
    };
    document.addEventListener("visibilitychange", visibility);
    return () => {
      generation.current++;
      abortRef.current?.abort();
      releaseVideo(decoderRef.current);
      releaseVideo(previewRef.current);
      if (selectionRef.current) URL.revokeObjectURL(selectionRef.current.url);
      selectionRef.current = null;
      document.removeEventListener("visibilitychange", visibility);
    };
  }, []);

  useEffect(() => {
    if (state !== "loading" || !selection) return;
    const activeUrl = selection.url;
    const timer = setTimeout(() => {
      if (selectionRef.current?.url !== activeUrl) return;
      releaseVideo(previewRef.current);
      setMetadata(null);
      setState("error");
      setError(
        "Video metadata could not be loaded. Try a shorter MP4 (H.264) or WebM clip.",
      );
    }, 12_000);
    return () => clearTimeout(timer);
  }, [state, selection]);

  function clearFile(resetPicker = true) {
    generation.current++;
    abortRef.current?.abort();
    abortRef.current = null;
    releaseVideo(decoderRef.current);
    decoderRef.current = null;
    releaseVideo(previewRef.current);
    if (selectionRef.current) URL.revokeObjectURL(selectionRef.current.url);
    selectionRef.current = null;
    setSelection(null);
    setMetadata(null);
    setSegments([]);
    setProgress(0);
    setSampleCount(0);
    setSampledThrough(0);
    setPlayhead(0);
    setError("");
    setState("empty");
    if (resetPicker && inputRef.current) inputRef.current.value = "";
  }

  function chooseFile(file: File | undefined) {
    if (!file) return;
    const invalid = validateVideoFile(file);
    clearFile(Boolean(invalid));
    if (invalid) {
      setError(invalid);
      return;
    }
    const selected = {
      url: URL.createObjectURL(file),
      name: file.name,
      bytes: file.size,
    };
    selectionRef.current = selected;
    setSelection(selected);
    setState("loading");
  }

  function onMetadata(video: HTMLVideoElement, url: string) {
    if (selectionRef.current?.url !== url) return;
    const info = {
      duration: video.duration,
      width: video.videoWidth,
      height: video.videoHeight,
    };
    const invalid = validateVideoMetadata(info);
    if (invalid) {
      releaseVideo(video);
      setError(invalid);
      setState("error");
      setMetadata(null);
      return;
    }
    setMetadata(info);
    setState("ready");
    setError("");
  }

  async function analyse() {
    const file = selectionRef.current;
    if (!file || !metadata || running) return;
    const epoch = ++generation.current;
    const current = () =>
      epoch === generation.current && selectionRef.current?.url === file.url;
    const controller = new AbortController();
    abortRef.current?.abort();
    abortRef.current = controller;
    const video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.preload = "auto";
    decoderRef.current = video;
    previewRef.current?.pause();
    setSegments([]);
    setSampleCount(0);
    setSampledThrough(0);
    setProgress(0);
    setError("");
    setState("analysing");
    let timedOut = false;
    const deadline = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, ANALYSIS_TIMEOUT_MS);
    try {
      await mediaStep(video, "loadeddata", controller.signal, () => {
        video.src = file.url;
        video.load();
      });
      const invalid = validateVideoMetadata({
        duration: video.duration,
        width: video.videoWidth,
        height: video.videoHeight,
      });
      if (invalid) throw new Error(invalid);
      const canvas = document.createElement("canvas");
      canvas.width = 160;
      canvas.height = Math.max(
        1,
        Math.min(160, Math.round((160 * video.videoHeight) / video.videoWidth)),
      );
      const context = canvas.getContext("2d", { willReadFrequently: true });
      if (!context)
        throw new Error("This browser cannot start local frame analysis.");
      const total = Math.min(
        VIDEO_LIMITS.maxSamples,
        Math.ceil(video.duration / VIDEO_LIMITS.sampleInterval),
      );
      const samples: ActivitySample[] = [];
      let previous: Uint8Array | null = null;
      for (let i = 0; i < total; i++) {
        if (controller.signal.aborted || !current())
          throw new DOMException("Analysis cancelled", "AbortError");
        const target = Math.min(
          i * VIDEO_LIMITS.sampleInterval,
          video.duration - 0.001,
        );
        if (
          Math.abs(video.currentTime - target) > 0.001 ||
          video.readyState < 2
        ) {
          await mediaStep(video, "seeked", controller.signal, () => {
            video.currentTime = target;
          });
        }
        if (controller.signal.aborted || !current())
          throw new DOMException("Analysis cancelled", "AbortError");
        if (video.readyState < 2) throw new Error(DECODE_ERROR);
        context.drawImage(video, 0, 0, canvas.width, canvas.height);
        const luminance = rgbaToLuminance(
          context.getImageData(0, 0, canvas.width, canvas.height).data,
        );
        samples.push({
          time: target,
          changedRatio: previous ? changedPixelRatio(previous, luminance) : 0,
        });
        previous = luminance;
        setProgress(Math.round(((i + 1) / total) * 100));
        setSampleCount(i + 1);
        setSampledThrough(target);
        // Yield between decode requests so cancellation and normal UI work remain responsive.
        await new Promise<void>((resolve) => setTimeout(resolve, 0));
      }
      if (controller.signal.aborted || !current())
        throw new DOMException("Analysis cancelled", "AbortError");
      setSegments(groupActivitySamples(samples));
      setState("complete");
    } catch (err) {
      if (current()) {
        setSegments([]);
        if (timedOut) {
          setError(
            "Analysis reached its two-minute processing limit. Try a shorter clip.",
          );
          setState("error");
        } else if (controller.signal.aborted) {
          setState("cancelled");
        } else {
          setError(err instanceof Error ? err.message : DECODE_ERROR);
          setState("error");
        }
      }
    } finally {
      clearTimeout(deadline);
      releaseVideo(video);
      if (decoderRef.current === video) decoderRef.current = null;
      if (abortRef.current === controller) abortRef.current = null;
    }
  }

  function jump(seconds: number) {
    const preview = previewRef.current;
    if (!preview || !metadata) return;
    preview.pause();
    preview.currentTime = Math.max(0, Math.min(seconds, metadata.duration));
    setPlayhead(seconds);
    preview.focus();
  }

  function downloadSummary() {
    if (!selection || !metadata || state !== "complete") return;
    const report = {
      format: "AISLESIGNALS_VIDEO_ACTIVITY_TEST_V1",
      mode: "RECORDED_PLAYBACK_TEST",
      branch_name: branchName,
      file_name: selection.name,
      duration_seconds: metadata.duration,
      analysis: "Deterministic frame difference; no AI or theft classification",
      sample_interval_seconds: VIDEO_LIMITS.sampleInterval,
      sampled_through_seconds: sampledThrough,
      samples: sampleCount,
      activity_segments: segments,
      result_limit: VIDEO_LIMITS.maxResults,
      limitations:
        "Whole-frame visual changes, including lighting or camera movement. Short or subtle events may be missed. No activity does not establish safety or absence of an incident. Not an evidence export or live alert.",
    };
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = "aislesignals-video-test.json";
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1_000);
  }

  const status = {
    empty: "Choose a recording to begin",
    loading: "Loading video…",
    ready: "Video ready",
    analysing: "Analysing video…",
    complete: "Analysis complete",
    cancelled: "Analysis cancelled",
    error: "Video test needs attention",
  }[state];

  return (
    <div className="video-test-page">
      <div className="notice amber">
        <Film size={19} />
        <span>
          <strong>Recorded playback test</strong> · No live alerts. The local
          scan detects sustained visual changes; AI theft detection and person
          recognition are not enabled.
        </span>
      </div>
      <div className="video-test-grid">
        <section
          className="panel video-test-player-panel"
          aria-labelledby="video-file-title"
        >
          <div className="panel-header">
            <div>
              <h2 id="video-file-title">Load a test video</h2>
              <p>Choose a recording you are authorised to use.</p>
            </div>
            <span className="badge badge-neutral">Local only</span>
          </div>
          <div
            className={`video-file-picker ${dragging ? "dragging" : ""}`}
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              if (event.dataTransfer.files.length !== 1) {
                setError("Choose one video at a time.");
                return;
              }
              if (inputRef.current)
                inputRef.current.files = event.dataTransfer.files;
              chooseFile(event.dataTransfer.files[0]);
            }}
          >
            <label htmlFor="video-test-file">Choose a video</label>
            <input
              ref={inputRef}
              id="video-test-file"
              type="file"
              accept="video/mp4,video/webm,.mp4,.webm"
              onChange={(event) => chooseFile(event.currentTarget.files?.[0])}
              aria-describedby="video-file-limits"
            />
            <p id="video-file-limits">
              Drop one MP4 or WebM here. Up to 250 MB, 10 minutes and 4K frame
              size. Playback depends on the file’s codec and your browser.
            </p>
          </div>
          {error && (
            <div className="video-test-error" role="alert">
              {error}
            </div>
          )}
          {selection ? (
            <>
              <div className="video-file-summary">
                <div>
                  <strong>{selection.name}</strong>
                  <span>
                    {selection.bytes < 1024 * 1024
                      ? `${Math.ceil(selection.bytes / 1024)} KB`
                      : `${(selection.bytes / 1024 / 1024).toFixed(1)} MB`}
                    {metadata &&
                      ` · ${timestamp(metadata.duration)} · ${metadata.width} × ${metadata.height}`}
                  </span>
                </div>
                <button
                  className="icon-button"
                  aria-label="Remove video"
                  onClick={() => clearFile()}
                >
                  <X size={19} />
                </button>
              </div>
              <div className="video-preview-wrap">
                <video
                  key={selection.url}
                  ref={bindPreview}
                  src={selection.url}
                  controls
                  muted
                  playsInline
                  preload="metadata"
                  disablePictureInPicture
                  disableRemotePlayback
                  controlsList="nodownload noremoteplayback"
                  aria-label="Selected test video"
                  onLoadedMetadata={(event) =>
                    onMetadata(event.currentTarget, selection.url)
                  }
                  onTimeUpdate={(event) => {
                    if (selectionRef.current?.url === selection.url)
                      setPlayhead(event.currentTarget.currentTime);
                  }}
                  onError={() => {
                    if (selectionRef.current?.url !== selection.url) return;
                    generation.current++;
                    abortRef.current?.abort();
                    setMetadata(null);
                    setState("error");
                    setError(DECODE_ERROR);
                  }}
                />
                <span className="video-playback-tag">PLAYBACK TEST</span>
              </div>
              <div className="video-test-actions">
                {running ? (
                  <button
                    className="button secondary"
                    onClick={() => abortRef.current?.abort()}
                  >
                    <Square size={16} />
                    Cancel analysis
                  </button>
                ) : (
                  <button
                    className="button primary"
                    disabled={!metadata}
                    onClick={() => void analyse()}
                  >
                    <Activity size={17} />
                    Analyse video
                  </button>
                )}
                <span className="muted">
                  Playback position {timestamp(playhead)}
                </span>
              </div>
            </>
          ) : (
            <div className="video-empty">
              <Film size={36} />
              <h3>Your recording stays in this tab</h3>
              <p>
                Preview the video, scan for visual activity and inspect the
                timestamps. Nothing is uploaded to a server.
              </p>
            </div>
          )}
          <div className="video-test-progress" role="status" aria-live="polite">
            <strong>{status}</strong>
            {running && (
              <progress
                value={progress}
                max="100"
                aria-label="Video analysis progress"
              />
            )}
            {(running || state === "complete") && (
              <span>
                {progress}% · {sampleCount} frames sampled through{" "}
                {timestamp(sampledThrough)}
              </span>
            )}
            {state === "cancelled" && (
              <p>
                No complete result was produced. Run the analysis again when
                this tab is visible.
              </p>
            )}
          </div>
        </section>
        <section
          className="panel video-timeline-panel"
          aria-labelledby="video-timeline-title"
        >
          <div className="panel-header">
            <div>
              <h2 id="video-timeline-title">Activity timeline</h2>
              <p>Timestamps in the selected recording.</p>
            </div>
          </div>
          {state === "complete" ? (
            <>
              {segments.length ? (
                <>
                  <p className="video-results-count">
                    {segments.length} visual activity{" "}
                    {segments.length === 1 ? "segment" : "segments"}
                  </p>
                  <ol className="video-timeline-list">
                    {segments.map((segment, index) => (
                      <li key={`${index}-${segment.start}`}>
                        <button
                          onClick={() => jump(segment.start)}
                          aria-label={`Jump to ${timestamp(segment.start)}`}
                        >
                          <Play size={16} />
                          <div>
                            <strong>
                              {timestamp(segment.start)} –{" "}
                              {timestamp(segment.end)}
                            </strong>
                            <span>
                              Visual activity · frame change{" "}
                              {Math.round(segment.peakChangedRatio * 100)}%
                            </span>
                          </div>
                        </button>
                      </li>
                    ))}
                  </ol>
                  {segments.length >= VIDEO_LIMITS.maxResults && (
                    <p className="video-result-note">
                      Showing the first {VIDEO_LIMITS.maxResults} segments. This
                      result may be truncated.
                    </p>
                  )}
                </>
              ) : (
                <div className="video-empty">
                  <Activity size={29} />
                  <h3>No sustained visual activity found</h3>
                  <p>
                    This does not establish that the recording is safe or
                    contains no incident. Review the video yourself.
                  </p>
                </div>
              )}
              <div className="video-report-action">
                <button className="button secondary" onClick={downloadSummary}>
                  <ArrowDownToLine size={16} />
                  Download test summary
                </button>
              </div>
            </>
          ) : (
            <div className="video-empty">
              <Activity size={29} />
              <h3>
                {running
                  ? "Scanning the recording"
                  : "Results appear after analysis"}
              </h3>
              <p>
                {running
                  ? "The video is processed locally. You can cancel at any time."
                  : "Run a scan to build a timeline, then select a timestamp to inspect the video."}
              </p>
            </div>
          )}
          <div className="video-method-note">
            <ShieldCheck size={18} />
            <div>
              <strong>Understand this test</strong>
              <p>
                Samples two frames per second using whole-frame image
                differences. Lighting changes and camera movement can trigger
                results; small or brief actions may be missed. Frame-change
                percentages are not confidence in theft.
              </p>
              <p>
                No camera connection, person identification, paid AI call,
                incident creation or alarm is triggered. The file and results
                are cleared when you remove the video, leave this page or sign
                out. Downloaded summaries remain on your device.
              </p>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
