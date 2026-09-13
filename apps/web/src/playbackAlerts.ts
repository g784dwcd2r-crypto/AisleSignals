import type { ActivitySegment } from "./videoActivity";

/** These are pixel-change descriptions, never interaction or intent findings. */
export type ActivityClassificationCode =
  | "SUSTAINED_VISUAL_ACTIVITY"
  | "EXTENDED_VISUAL_ACTIVITY"
  | "LARGE_SCENE_CHANGE";

export interface ActivityClassification {
  code: ActivityClassificationCode;
  label: string;
  detail: string;
}

function validSegment(segment: ActivitySegment): boolean {
  return (
    Number.isFinite(segment.start) &&
    Number.isFinite(segment.end) &&
    segment.start >= 0 &&
    segment.end > segment.start &&
    Number.isFinite(segment.peakChangedRatio) &&
    segment.peakChangedRatio >= 0 &&
    segment.peakChangedRatio <= 1
  );
}

export function classifyActivity(
  segment: ActivitySegment,
): ActivityClassification {
  if (!validSegment(segment))
    throw new RangeError("Expected a valid visual-activity segment.");
  if (segment.peakChangedRatio >= 0.65)
    return {
      code: "LARGE_SCENE_CHANGE",
      label: "Large scene change",
      detail:
        "At least 65% of sampled pixels changed. Lighting, camera movement or a scene cut may explain this; the interaction is unknown.",
    };
  if (segment.end - segment.start >= 5)
    return {
      code: "EXTENDED_VISUAL_ACTIVITY",
      label: "Extended visual activity",
      detail:
        "A confirmed visual-change interval spans at least five seconds. This does not identify a person, action or intent.",
    };
  return {
    code: "SUSTAINED_VISUAL_ACTIVITY",
    label: "Sustained visual activity",
    detail:
      "Consecutive sampled frames changed. Staff must review the recording to determine the interaction; theft is not inferred.",
  };
}

export interface PlaybackPosition {
  currentTime: number;
  /** Monotonic wall clock, normally performance.now(). */
  nowMs: number;
  playing: boolean;
  visible: boolean;
  seeking: boolean;
  playbackRate: number;
}

/**
 * One notification per segment per explicit armed run. Use only completed scans.
 * The first sample establishes continuity. Seeking, pausing, hidden tabs, stalls,
 * unsupported speed and late callbacks cannot produce historical catch-up alerts.
 * A segment landed inside following a discontinuity is skipped for this run.
 */
export class PlaybackAlertGate {
  private readonly segments: readonly ActivitySegment[];
  private armed = false;
  private previous: PlaybackPosition | null = null;
  private handled = new Set<number>();

  constructor(segments: readonly ActivitySegment[]) {
    if (segments.length > 100 || segments.some((item) => !validSegment(item)))
      throw new RangeError("Expected at most 100 valid completed segments.");
    this.segments = segments.map((segment) => ({ ...segment }));
  }

  arm(): void {
    this.armed = true;
    this.handled.clear();
    this.previous = null;
  }

  disarm(): void {
    this.armed = false;
    this.previous = null;
  }

  resetContinuity(): void {
    this.previous = null;
  }

  private skipContaining(time: number): void {
    this.segments.forEach((segment, index) => {
      if (time > segment.start && time <= segment.end) this.handled.add(index);
    });
  }

  update(position: PlaybackPosition): number[] {
    if (!this.armed) return [];
    const valid =
      Number.isFinite(position.currentTime) &&
      position.currentTime >= 0 &&
      Number.isFinite(position.nowMs) &&
      position.nowMs >= 0;
    if (!valid) {
      this.previous = null;
      return [];
    }
    if (
      !position.playing ||
      !position.visible ||
      position.seeking ||
      position.playbackRate !== 1
    ) {
      this.skipContaining(position.currentTime);
      this.previous = null;
      return [];
    }
    const previous = this.previous;
    this.previous = { ...position };
    if (!previous) {
      this.skipContaining(position.currentTime);
      return [];
    }
    const wallDelta = (position.nowMs - previous.nowMs) / 1000;
    const mediaDelta = position.currentTime - previous.currentTime;
    if (
      wallDelta <= 0 ||
      wallDelta > 1 ||
      mediaDelta < 0 ||
      mediaDelta > 1 ||
      Math.abs(mediaDelta - wallDelta) > 0.35
    ) {
      this.skipContaining(position.currentTime);
      return [];
    }
    if (mediaDelta === 0) return [];
    const matches: number[] = [];
    this.segments.forEach((segment, index) => {
      if (
        !this.handled.has(index) &&
        position.currentTime >= segment.start &&
        previous.currentTime < segment.end
      ) {
        this.handled.add(index);
        matches.push(index);
      }
    });
    return matches;
  }
}

export const ATTENTION_SOUND_LIMITS = Object.freeze({
  defaultVolume: 0.35,
  maxVolume: 1,
  maxDurationSeconds: 8,
});

export type AttentionSoundResult =
  | { ok: true; message: string; audible: "unverified" }
  | { ok: false; message: string };

export function clampAttentionVolume(volume: number): number {
  return Number.isFinite(volume)
    ? Math.min(ATTENTION_SOUND_LIMITS.maxVolume, Math.max(0, volume))
    : ATTENTION_SOUND_LIMITS.defaultVolume;
}

function defaultAudioContext(): AudioContext {
  const browser = globalThis as typeof globalThis & {
    webkitAudioContext?: typeof AudioContext;
  };
  const Constructor = browser.AudioContext ?? browser.webkitAudioContext;
  if (!Constructor) throw new Error("Web Audio is unavailable.");
  return new Constructor();
}

/**
 * Existing-browser-speaker test only. arm() must run directly from a user gesture.
 * A running AudioContext cannot establish speaker volume or physical audibility.
 * Call stop() on mute, pause, seek and visibility loss; disarm()/dispose() on exit.
 */
export class BrowserAttentionSound {
  private context: AudioContext | null = null;
  private oscillator: OscillatorNode | null = null;
  private gain: GainNode | null = null;
  private pulseGain: GainNode | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private armed = false;
  private disposed = false;
  private generation = 0;
  private volume: number = ATTENTION_SOUND_LIMITS.defaultVolume;

  constructor(
    private readonly createContext: () => AudioContext = defaultAudioContext,
  ) {}

  async arm(): Promise<AttentionSoundResult> {
    if (this.disposed)
      return { ok: false, message: "The sound test has been closed." };
    const generation = ++this.generation;
    this.armed = false;
    this.stop();
    let timeout: ReturnType<typeof setTimeout> | null = null;
    try {
      const context = (this.context ??= this.createContext());
      context.onstatechange = () => {
        if (context.state !== "running") {
          this.armed = false;
          this.stop();
        }
      };
      await Promise.race([
        context.resume(),
        new Promise<never>((_, reject) => {
          timeout = setTimeout(
            () => reject(new Error("Audio activation timed out.")),
            1500,
          );
        }),
      ]);
      if (this.disposed || generation !== this.generation)
        return { ok: false, message: "Sound activation was cancelled." };
      if (context.state !== "running")
        throw new Error("The browser has not enabled audio output.");
      this.armed = true;
      return {
        ok: true,
        audible: "unverified",
        message:
          "Browser audio enabled. Confirm the test tone is audible on this laptop.",
      };
    } catch {
      return {
        ok: false,
        message:
          "Browser audio could not be enabled. Use the sound button again and check the browser and laptop sound settings.",
      };
    } finally {
      if (timeout !== null) clearTimeout(timeout);
    }
  }

  setVolume(volume: number): void {
    this.volume = clampAttentionVolume(volume);
    if (this.volume === 0) this.stop();
    else if (this.gain && this.context)
      this.gain.gain.setValueAtTime(
        this.volume * 0.35,
        this.context.currentTime,
      );
  }

  play(
    options: {
      volume?: number;
      durationSeconds?: number;
    } = {},
  ): AttentionSoundResult {
    this.stop();
    if (options.volume !== undefined) this.setVolume(options.volume);
    const context = this.context;
    if (!this.armed || this.disposed || context?.state !== "running")
      return {
        ok: false,
        message: "Sound is unavailable. Enable it using the sound test button.",
      };
    if (this.volume === 0)
      return {
        ok: false,
        message: "Sound is muted; the visual alert remains.",
      };
    const requested = options.durationSeconds ?? 8;
    const duration = Number.isFinite(requested)
      ? Math.min(8, Math.max(0.1, requested))
      : 8;
    try {
      const oscillator = (this.oscillator = context.createOscillator());
      const gain = (this.gain = context.createGain());
      const pulseGain = (this.pulseGain = context.createGain());
      oscillator.type = "sine";
      oscillator.connect(pulseGain);
      pulseGain.connect(gain);
      gain.connect(context.destination);
      const start = context.currentTime + 0.015;
      gain.gain.setValueAtTime(this.volume * 0.35, context.currentTime);
      pulseGain.gain.setValueAtTime(0, context.currentTime);
      for (
        let offset = 0, pulse = 0;
        offset < duration;
        offset += 0.4, pulse++
      ) {
        if (duration - offset < 0.025) break;
        const at = start + offset;
        const end = Math.min(at + 0.28, start + duration);
        oscillator.frequency.setValueAtTime(pulse % 2 === 0 ? 740 : 980, at);
        pulseGain.gain.setValueAtTime(0, at);
        pulseGain.gain.linearRampToValueAtTime(1, at + 0.01);
        pulseGain.gain.setValueAtTime(1, Math.max(at + 0.01, end - 0.02));
        pulseGain.gain.linearRampToValueAtTime(0, end);
      }
      oscillator.start(start);
      oscillator.stop(start + duration);
      oscillator.onended = () => {
        if (this.oscillator === oscillator) this.stop();
      };
      this.timer = setTimeout(() => this.stop(), duration * 1000 + 50);
      return {
        ok: true,
        audible: "unverified",
        message:
          "Attention tone requested on this laptop; actual audibility is unverified.",
      };
    } catch {
      this.stop();
      return {
        ok: false,
        message:
          "The browser could not start the tone. The visual alert remains.",
      };
    }
  }

  stop(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    const oscillator = this.oscillator;
    const gain = this.gain;
    const pulseGain = this.pulseGain;
    this.oscillator = null;
    this.gain = null;
    this.pulseGain = null;
    if (gain) {
      try {
        gain.gain.cancelScheduledValues(0);
        gain.gain.setValueAtTime(0, this.context?.currentTime ?? 0);
      } catch {
        // Disconnect below remains the stop fallback for a closed context.
      }
      gain.disconnect();
    }
    pulseGain?.disconnect();
    if (oscillator) {
      oscillator.onended = null;
      try {
        oscillator.stop();
      } catch {
        // Already stopped or not yet started. Disconnect in either case.
      }
      oscillator.disconnect();
    }
  }

  disarm(): void {
    this.generation++;
    this.armed = false;
    this.stop();
  }

  dispose(): void {
    this.disposed = true;
    this.disarm();
    const context = this.context;
    this.context = null;
    if (context) {
      context.onstatechange = null;
      void context.close().catch(() => undefined);
    }
  }
}
