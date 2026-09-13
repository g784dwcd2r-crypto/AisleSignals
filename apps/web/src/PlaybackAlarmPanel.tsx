import { useEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import { BellRing, VolumeX, Square } from "lucide-react";
import { api } from "./api";
import type { ActivitySegment } from "./videoActivity";
import {
  BrowserAttentionSound,
  PlaybackAlertGate,
  classifyActivity,
} from "./playbackAlerts";

type EventInput = {
  run_id: string;
  event_index: number;
  category: string;
  video_start_seconds: number;
  video_end_seconds: number;
  peak_changed_ratio: number;
  alarm_status: "SOUND_REQUESTED" | "MUTED" | "BLOCKED";
};
type SavedEvent = EventInput & { id: string; recorded_at: string };
function time(value: number) {
  return `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, "0")}`;
}
const labels: Record<string, string> = {
  SUSTAINED_VISUAL_ACTIVITY: "Sustained visual activity",
  EXTENDED_VISUAL_ACTIVITY: "Extended visual activity",
  LARGE_SCENE_CHANGE: "Large scene change",
};

/** Every preparation wait is bounded and is released on stop or view teardown. */
function waitForVideoEvent(
  video: HTMLVideoElement,
  event: "pause" | "ratechange" | "seeked",
  signal: AbortSignal,
  action: () => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Playback start cancelled", "AbortError"));
      return;
    }
    const cleanup = () => {
      clearTimeout(timer);
      video.removeEventListener(event, done);
      video.removeEventListener("error", failed);
      signal.removeEventListener("abort", cancelled);
    };
    const done = () => {
      cleanup();
      resolve();
    };
    const failed = () => {
      cleanup();
      reject(new Error("This recording could not prepare for playback."));
    };
    const cancelled = () => {
      cleanup();
      reject(new DOMException("Playback start cancelled", "AbortError"));
    };
    const timer = setTimeout(() => {
      cleanup();
      reject(
        new Error("Playback preparation timed out. Try the recording again."),
      );
    }, 8000);
    video.addEventListener(event, done, { once: true });
    video.addEventListener("error", failed, { once: true });
    signal.addEventListener("abort", cancelled, { once: true });
    try {
      action();
    } catch {
      failed();
    }
  });
}

function waitForPlaybackStart(
  playback: Promise<void>,
  signal: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", cancelled);
    };
    const cancelled = () => {
      cleanup();
      reject(new DOMException("Playback start cancelled", "AbortError"));
    };
    const timer = setTimeout(() => {
      cleanup();
      reject(
        new Error("Playback did not start in time. Try the recording again."),
      );
    }, 8000);
    signal.addEventListener("abort", cancelled, { once: true });
    // Always consume the original promise, including a late rejection after abort.
    void playback.then(
      () => {
        cleanup();
        resolve();
      },
      (error: unknown) => {
        cleanup();
        reject(error);
      },
    );
    if (signal.aborted) cancelled();
  });
}

export default function PlaybackAlarmPanel({
  videoRef,
  segments,
}: {
  videoRef: RefObject<HTMLVideoElement | null>;
  segments: ActivitySegment[] | null;
}) {
  const [armed, setArmed] = useState(false);
  const [starting, setStarting] = useState(false);
  const [volume, setVolume] = useState(0.65);
  const [muted, setMuted] = useState(false);
  const [notice, setNotice] = useState(
    "Analyse a video, then start an alarm playback test.",
  );
  const [attention, setAttention] = useState("");
  const [events, setEvents] = useState<SavedEvent[]>([]);
  const [pending, setPending] = useState<EventInput[]>([]);
  const [logError, setLogError] = useState("");
  const [saving, setSaving] = useState(0);
  const sound = useRef<BrowserAttentionSound | null>(null);
  const gate = useRef<PlaybackAlertGate | null>(null);
  const armedRef = useRef(false);
  const mutedRef = useRef(false);
  const volumeRef = useRef(volume);
  const runId = useRef("");
  const lifetime = useRef(0);
  const startEpoch = useRef(0);
  const startup = useRef<AbortController | null>(null);

  function stop(message = "Alarm playback stopped.") {
    startEpoch.current++;
    startup.current?.abort();
    startup.current = null;
    armedRef.current = false;
    gate.current?.disarm();
    sound.current?.disarm();
    videoRef.current?.pause();
    setArmed(false);
    setStarting(false);
    setAttention("");
    setNotice(message);
  }

  async function refreshLog() {
    const epoch = lifetime.current;
    try {
      const items = await api<SavedEvent[]>("/playback-events");
      if (epoch !== lifetime.current) return;
      setEvents((current) =>
        [
          ...items,
          ...current.filter(
            (item) => !items.some((saved) => saved.id === item.id),
          ),
        ]
          .sort((a, b) => b.recorded_at.localeCompare(a.recorded_at))
          .slice(0, 100),
      );
      setLogError("");
    } catch {
      if (epoch === lifetime.current)
        setLogError(
          "Could not load saved playback events. Retry when the local app is available.",
        );
    }
  }

  async function save(input: EventInput) {
    const epoch = lifetime.current;
    setSaving((n) => n + 1);
    try {
      const saved = await api<SavedEvent>("/playback-events", "POST", input);
      if (epoch !== lifetime.current) return;
      setEvents((items) =>
        [saved, ...items.filter((item) => item.id !== saved.id)].slice(0, 100),
      );
      setPending((items) =>
        items.filter(
          (item) =>
            item.run_id !== input.run_id ||
            item.event_index !== input.event_index,
        ),
      );
    } catch {
      if (epoch !== lifetime.current) return;
      setPending((items) =>
        items.some(
          (item) =>
            item.run_id === input.run_id &&
            item.event_index === input.event_index,
        )
          ? items
          : [...items, input].slice(0, 100),
      );
      stop(
        "Saving failed. Alarm playback stopped; retry the unsaved event below.",
      );
    } finally {
      if (epoch === lifetime.current) setSaving((n) => Math.max(0, n - 1));
    }
  }

  useEffect(() => {
    const audio = new BrowserAttentionSound();
    sound.current = audio;
    const hidden = () => {
      if (document.hidden)
        stop(
          "Alarm playback stopped because this tab is hidden. Start it again when ready.",
        );
    };
    document.addEventListener("visibilitychange", hidden);
    void refreshLog();
    return () => {
      lifetime.current++;
      startEpoch.current++;
      startup.current?.abort();
      startup.current = null;
      armedRef.current = false;
      gate.current?.disarm();
      audio.dispose();
      videoRef.current?.pause();
      document.removeEventListener("visibilitychange", hidden);
    };
    // Mounted once for this authenticated branch; unsaved metadata survives file replacement.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    stop("Analyse a video, then start an alarm playback test.");
    gate.current = new PlaybackAlertGate(segments ?? []);
    const video = videoRef.current;
    if (!video || !segments) return;
    let frame = 0;
    const tick = () => {
      if (armedRef.current && gate.current) {
        const triggered = gate.current.update({
          currentTime: video.currentTime,
          nowMs: performance.now(),
          playing: !video.paused && !video.ended,
          visible: !document.hidden,
          seeking: video.seeking,
          playbackRate: video.playbackRate,
        });
        for (const index of triggered) {
          const segment = segments[index];
          const classification = classifyActivity(segment);
          setAttention(
            `${classification.label} at ${time(segment.start)} — review the recording`,
          );
          const result = mutedRef.current
            ? null
            : sound.current?.play({
                volume: volumeRef.current,
                durationSeconds: 8,
              });
          const alarmStatus = mutedRef.current
            ? "MUTED"
            : result?.ok
              ? "SOUND_REQUESTED"
              : "BLOCKED";
          setNotice(
            alarmStatus === "SOUND_REQUESTED"
              ? "Attention tone requested for up to 8 seconds. Actual loudness depends on your laptop and selected speakers."
              : alarmStatus === "MUTED"
                ? "Visual alert recorded; sound is muted."
                : "Sound was blocked or unavailable. The visual alert remains visible.",
          );
          void save({
            run_id: runId.current,
            event_index: index,
            category: classification.code,
            video_start_seconds: segment.start,
            video_end_seconds: segment.end,
            peak_changed_ratio: segment.peakChangedRatio,
            alarm_status: alarmStatus,
          });
        }
      }
      frame = requestAnimationFrame(tick);
    };
    const discontinuity = () => {
      sound.current?.stop();
      gate.current?.resetContinuity();
    };
    const ended = () =>
      stop("Alarm playback finished. Events remain in the branch test log.");
    video.addEventListener("seeking", discontinuity);
    video.addEventListener("pause", discontinuity);
    video.addEventListener("ratechange", discontinuity);
    video.addEventListener("ended", ended);
    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      armedRef.current = false;
      startEpoch.current++;
      startup.current?.abort();
      startup.current = null;
      gate.current?.disarm();
      sound.current?.disarm();
      video.removeEventListener("seeking", discontinuity);
      video.removeEventListener("pause", discontinuity);
      video.removeEventListener("ratechange", discontinuity);
      video.removeEventListener("ended", ended);
    };
    // Rebind only after a complete analysis; progress never rings or saves events.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [segments]);

  async function start() {
    const video = videoRef.current;
    if (
      !video ||
      !segments?.length ||
      document.hidden ||
      armedRef.current ||
      startup.current !== null ||
      starting ||
      pending.length ||
      saving
    )
      return;
    const epoch = ++startEpoch.current;
    const controller = new AbortController();
    startup.current = controller;
    const current = () =>
      !controller.signal.aborted &&
      epoch === startEpoch.current &&
      videoRef.current === video &&
      !document.hidden;
    setStarting(true);
    setAttention("");
    runId.current = crypto.randomUUID();
    // Resume browser audio directly from the click. Hold the recording still while
    // that bounded activation settles, so a delayed resume cannot skip activity.
    video.muted = true;
    const paused = video.paused
      ? Promise.resolve()
      : waitForVideoEvent(video, "pause", controller.signal, () =>
          video.pause(),
        );
    const unlock = sound.current!.arm();
    try {
      const [audio] = await Promise.all([unlock, paused]);
      if (!current()) return;
      // Native controls may have been used while audio activation was pending.
      if (!video.paused)
        await waitForVideoEvent(video, "pause", controller.signal, () =>
          video.pause(),
        );
      if (video.playbackRate !== 1)
        await waitForVideoEvent(video, "ratechange", controller.signal, () => {
          video.playbackRate = 1;
        });
      if (video.currentTime !== 0 || video.seeking)
        await waitForVideoEvent(video, "seeked", controller.signal, () => {
          video.currentTime = 0;
        });
      if (!current()) return;
      if (!video.paused || video.seeking || video.currentTime !== 0)
        throw new Error(
          "The recording moved during preparation. Start the test again.",
        );
      gate.current?.arm();
      // A muted recording can start after the asynchronous rewind. play() changes
      // paused synchronously; record its actual position before it can advance.
      const playback = video.play();
      gate.current?.update({
        currentTime: video.currentTime,
        nowMs: performance.now(),
        playing: !video.paused && !video.ended,
        visible: !document.hidden,
        seeking: video.seeking,
        playbackRate: video.playbackRate,
      });
      armedRef.current = true;
      setArmed(true);
      await waitForPlaybackStart(playback, controller.signal);
      if (!current()) return;
      setNotice(
        audio.ok
          ? "Alarm playback armed. Watch the recording for a visual activity alert."
          : "Sound is unavailable. This run will show visual alerts and record the sound limitation.",
      );
    } catch (error) {
      if (epoch === startEpoch.current)
        stop(
          error instanceof Error && error.name !== "NotAllowedError"
            ? error.message
            : "Playback could not start. Use a supported file and try again.",
        );
    } finally {
      if (startup.current === controller) startup.current = null;
      if (epoch === startEpoch.current) setStarting(false);
    }
  }

  async function testSound() {
    const epoch = startEpoch.current;
    const result = await sound.current!.arm();
    if (epoch !== startEpoch.current || document.hidden || mutedRef.current)
      return;
    if (!result.ok) {
      setNotice(
        "The browser blocked sound. Check the browser and speaker settings.",
      );
      return;
    }
    const played = sound.current!.play({
      volume: volumeRef.current,
      durationSeconds: 2,
    });
    setNotice(
      played.ok
        ? "Two-second speaker test requested. Confirm you can hear it on your laptop."
        : "Sound could not start. Check your laptop output.",
    );
  }

  return (
    <section
      className="panel playback-alarm-panel"
      aria-labelledby="playback-alarm-title"
    >
      <div className="panel-header">
        <div>
          <h2 id="playback-alarm-title">
            Attention alarm & automatic test log
          </h2>
          <p>
            Replay an analysed recording at normal speed. A rule match triggers
            a visual alert, an optional tone and a saved test event.
          </p>
        </div>
        <BellRing size={23} />
      </div>
      <div className="playback-alarm-body">
        <p className="muted">
          Categories describe image changes. They do not identify stealing,
          concealment, aggression or a person’s intent. This is a pre-analysed
          playback test.
        </p>
        <div className="playback-alarm-controls">
          <label htmlFor="alarm-volume">
            Alarm volume: {Math.round(volume * 100)}%
            <input
              id="alarm-volume"
              type="range"
              min="10"
              max="100"
              step="5"
              value={Math.round(volume * 100)}
              onChange={(e) => {
                const value = Number(e.target.value) / 100;
                volumeRef.current = value;
                setVolume(value);
                sound.current?.setVolume(value);
              }}
            />
          </label>
          <label className="playback-mute">
            <input
              type="checkbox"
              checked={muted}
              onChange={(e) => {
                mutedRef.current = e.target.checked;
                setMuted(e.target.checked);
                if (e.target.checked) sound.current?.stop();
              }}
            />
            Mute alarm sound
          </label>
          <button
            className="button secondary"
            disabled={muted || armed || starting}
            onClick={() => void testSound()}
          >
            Test speaker · 2 seconds
          </button>
        </div>
        <div className="video-test-actions">
          <button
            className="button primary"
            disabled={
              !segments?.length ||
              armed ||
              starting ||
              pending.length > 0 ||
              saving > 0
            }
            onClick={() => void start()}
          >
            <BellRing size={17} />
            {starting ? "Starting playback…" : "Start alarm playback"}
          </button>
          <button className="button secondary" onClick={() => stop()}>
            <Square size={16} />
            Stop alarm test
          </button>
          <button
            className="button secondary"
            disabled={starting}
            onClick={() => {
              startEpoch.current++;
              sound.current?.stop();
              setAttention("");
            }}
          >
            <VolumeX size={16} />
            Silence current tone
          </button>
        </div>
        <div
          className={`playback-attention ${attention ? "active" : ""}`}
          role="status"
          aria-live="polite"
        >
          <strong>
            {attention ||
              (armed ? "Playback test armed" : "Playback test stopped")}
          </strong>
          <p>{notice}</p>
        </div>
        <p className="muted">
          The tone uses the current laptop output and never changes system
          volume. Seeking does not trigger a catch-up alarm. Pause, tab hiding
          and leaving this page stop sound.
        </p>
        <h3>Saved playback test events</h3>
        <p className="muted">
          Latest 100 events for this branch. Timestamps, categories and sound
          status are saved locally; video files and filenames are not sent.
          These records remain after sign-out.
        </p>
        {saving > 0 && <p role="status">Saving {saving} test event(s)…</p>}
        {pending.length > 0 && (
          <div className="video-test-error" role="alert">
            <strong>{pending.length} event(s) not confirmed saved.</strong>
            <p>
              Keep this page open and retry. The same event will not be saved
              twice.
            </p>
            <button
              className="button secondary"
              disabled={saving > 0}
              onClick={() => pending.forEach((item) => void save(item))}
            >
              Retry saving events
            </button>
          </div>
        )}
        {logError && <p role="alert">{logError}</p>}
        <button className="button secondary" onClick={() => void refreshLog()}>
          Refresh saved log
        </button>
        {events.length ? (
          <ol className="playback-event-list">
            {events.map((event) => (
              <li key={event.id}>
                <strong>{labels[event.category] ?? event.category}</strong>
                <span>
                  Recording {time(event.video_start_seconds)}–
                  {time(event.video_end_seconds)} ·{" "}
                  {event.alarm_status === "SOUND_REQUESTED"
                    ? "Sound requested; audibility unverified"
                    : event.alarm_status === "MUTED"
                      ? "Sound muted"
                      : "Sound blocked"}
                </span>
                <small>
                  Saved{" "}
                  {new Date(event.recorded_at).toLocaleString("en-IE", {
                    timeZone: "Europe/Dublin",
                  })}{" "}
                  · Playback test
                </small>
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted">No saved playback events yet.</p>
        )}
      </div>
    </section>
  );
}
