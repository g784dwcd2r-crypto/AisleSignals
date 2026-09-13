import { afterEach, describe, expect, it, vi } from "vitest";
import {
  BrowserAttentionSound,
  PlaybackAlertGate,
  classifyActivity,
  clampAttentionVolume,
} from "./playbackAlerts";
import type { PlaybackPosition } from "./playbackAlerts";

const segment = { start: 1, end: 3, peakChangedRatio: 0.3 };
const position = (
  currentTime: number,
  overrides: Partial<PlaybackPosition> = {},
): PlaybackPosition => ({
  currentTime,
  nowMs: currentTime * 1000,
  playing: true,
  visible: true,
  seeking: false,
  playbackRate: 1,
  ...overrides,
});

describe("descriptive visual-change classification", () => {
  it("labels duration and change magnitude without claiming an interaction", () => {
    expect(classifyActivity(segment).code).toBe("SUSTAINED_VISUAL_ACTIVITY");
    expect(classifyActivity({ ...segment, end: 6 }).code).toBe(
      "EXTENDED_VISUAL_ACTIVITY",
    );
    const broad = classifyActivity({ ...segment, peakChangedRatio: 0.65 });
    expect(broad.code).toBe("LARGE_SCENE_CHANGE");
    expect(broad.detail).toContain("interaction is unknown");
    expect(
      classifyActivity({ ...segment, end: 7, peakChangedRatio: 0.8 }).code,
    ).toBe("LARGE_SCENE_CHANGE");
  });
  it("rejects invalid intervals and ratios instead of creating a confident label", () => {
    for (const patch of [
      { start: -1 },
      { end: 1 },
      { end: Infinity },
      { peakChangedRatio: 1.01 },
      { peakChangedRatio: NaN },
    ])
      expect(() => classifyActivity({ ...segment, ...patch })).toThrow(
        RangeError,
      );
  });
});

describe("recorded playback attention gate", () => {
  it("requires explicit arming and continuous forward playback, then deduplicates", () => {
    const gate = new PlaybackAlertGate([segment]);
    expect(gate.update(position(1))).toEqual([]);
    gate.arm();
    expect(gate.update(position(0))).toEqual([]);
    expect(gate.update(position(0.5))).toEqual([]);
    expect(gate.update(position(1))).toEqual([0]);
    expect(gate.update(position(1.5))).toEqual([]);
    gate.update(position(0, { nowMs: 2000, seeking: true }));
    gate.update(position(0, { nowMs: 2100 }));
    gate.update(position(0.5, { nowMs: 2600 }));
    expect(gate.update(position(1, { nowMs: 3100 }))).toEqual([]);
    gate.arm();
    gate.update(position(0));
    gate.update(position(0.5));
    expect(gate.update(position(1))).toEqual([0]);
    gate.disarm();
    expect(gate.update(position(1.5))).toEqual([]);
  });
  it("allows a segment beginning exactly at the explicit playback start", () => {
    const gate = new PlaybackAlertGate([{ ...segment, start: 0 }]);
    gate.arm();
    expect(gate.update(position(0))).toEqual([]);
    expect(gate.update(position(0.25))).toEqual([0]);
  });
  it("skips the interval landed inside after seeking or an explicit discontinuity", () => {
    for (const explicitReset of [false, true]) {
      const gate = new PlaybackAlertGate([segment]);
      gate.arm();
      gate.update(position(0));
      if (explicitReset) gate.resetContinuity();
      else gate.update(position(1.5, { seeking: true, nowMs: 100 }));
      expect(gate.update(position(1.5, { nowMs: 200 }))).toEqual([]);
      expect(gate.update(position(1.75, { nowMs: 450 }))).toEqual([]);
    }
  });
  it("does not ring when paused, hidden, seeking, reversed or running at another speed", () => {
    for (const patch of [
      { playing: false },
      { visible: false },
      { seeking: true },
      { playbackRate: 2 },
      { playbackRate: -1 },
      { playbackRate: NaN },
    ]) {
      const gate = new PlaybackAlertGate([segment]);
      gate.arm();
      gate.update(position(0.5));
      expect(gate.update(position(1.2, patch))).toEqual([]);
      expect(gate.update(position(1.45))).toEqual([]);
      expect(gate.update(position(1.7))).toEqual([]);
    }
  });
  it("rejects stale callbacks and implausible jumps with no next-frame catch-up", () => {
    for (const sample of [
      position(1.5, { nowMs: 5000 }),
      position(1.5, { nowMs: 600 }),
      position(1.2, { nowMs: 400 }),
    ]) {
      const gate = new PlaybackAlertGate([segment]);
      gate.arm();
      gate.update(position(0.5));
      expect(gate.update(sample)).toEqual([]);
      expect(
        gate.update(
          position(sample.currentTime + 0.25, { nowMs: sample.nowMs + 250 }),
        ),
      ).toEqual([]);
    }
  });
  it("does not replay activity crossed during a long background gap", () => {
    const gate = new PlaybackAlertGate([
      { start: 1, end: 2, peakChangedRatio: 0.2 },
      { start: 4, end: 5, peakChangedRatio: 0.2 },
    ]);
    gate.arm();
    gate.update(position(0));
    expect(gate.update(position(3))).toEqual([]);
    expect(gate.update(position(3.5))).toEqual([]);
    expect(gate.update(position(4))).toEqual([1]);
  });
  it("suppresses backward motion and rejects malformed metadata", () => {
    const gate = new PlaybackAlertGate([segment]);
    gate.arm();
    gate.update(position(4));
    expect(gate.update(position(2, { nowMs: 4250 }))).toEqual([]);
    expect(gate.update(position(2.25, { nowMs: 4500 }))).toEqual([]);
    expect(gate.update(position(NaN))).toEqual([]);
    expect(gate.update(position(1.5))).toEqual([]);
    expect(gate.update(position(1.75))).toEqual([]);
    expect(() => new PlaybackAlertGate(Array(101).fill(segment))).toThrow();
    expect(() => new PlaybackAlertGate([{ ...segment, start: -1 }])).toThrow();
  });
  it("copies scan results so later caller mutations cannot manufacture alerts", () => {
    const mutable = { ...segment, start: 5, end: 6 };
    const gate = new PlaybackAlertGate([mutable]);
    mutable.start = 1;
    gate.arm();
    gate.update(position(0.5));
    expect(gate.update(position(1))).toEqual([]);
  });
});

function fakeAudio() {
  const parameter = () => ({
    setValueAtTime: vi.fn(),
    linearRampToValueAtTime: vi.fn(),
    cancelScheduledValues: vi.fn(),
  });
  const oscillators: ReturnType<typeof oscillator>[] = [];
  const gains: ReturnType<typeof gain>[] = [];
  const oscillator = () => ({
    type: "sine",
    frequency: parameter(),
    connect: vi.fn(),
    disconnect: vi.fn(),
    start: vi.fn(),
    stop: vi.fn(),
    onended: null as (() => void) | null,
  });
  const gain = () => ({
    gain: parameter(),
    connect: vi.fn(),
    disconnect: vi.fn(),
  });
  const context = {
    state: "suspended",
    currentTime: 10,
    destination: {},
    onstatechange: null as (() => void) | null,
    resume: vi.fn(async () => {
      context.state = "running";
    }),
    close: vi.fn(async () => {
      context.state = "closed";
    }),
    createOscillator: vi.fn(() => {
      const node = oscillator();
      oscillators.push(node);
      return node;
    }),
    createGain: vi.fn(() => {
      const node = gain();
      gains.push(node);
      return node;
    }),
  };
  return {
    context,
    oscillators,
    gains,
    engine: new BrowserAttentionSound(() => context as unknown as AudioContext),
  };
}

afterEach(() => vi.useRealTimers());

describe("bounded browser attention tone", () => {
  it("requires gesture activation and never asserts actual audibility", async () => {
    const { engine, context } = fakeAudio();
    expect(engine.play().ok).toBe(false);
    expect(context.resume).not.toHaveBeenCalled();
    expect(await engine.arm()).toMatchObject({
      ok: true,
      audible: "unverified",
    });
    expect(engine.play()).toMatchObject({ ok: true, audible: "unverified" });
    engine.dispose();
  });
  it("alternates attention frequencies, caps duration and allows immediate mute", async () => {
    vi.useFakeTimers();
    const { engine, oscillators, gains } = fakeAudio();
    await engine.arm();
    engine.play({ volume: 10, durationSeconds: 200 });
    const tone = oscillators[0];
    expect(tone.stop).toHaveBeenCalledWith(18.015);
    expect(tone.frequency.setValueAtTime.mock.calls[0][0]).toBe(740);
    expect(tone.frequency.setValueAtTime.mock.calls[1][0]).toBe(980);
    expect(gains[0].gain.setValueAtTime).toHaveBeenCalledWith(0.35, 10);
    engine.setVolume(0.5);
    expect(gains[0].gain.setValueAtTime).toHaveBeenLastCalledWith(0.175, 10);
    engine.setVolume(0);
    expect(tone.stop).toHaveBeenLastCalledWith();
    expect(tone.disconnect).toHaveBeenCalledOnce();
    expect(gains[0].gain.cancelScheduledValues).toHaveBeenCalledWith(0);
    expect(gains.every((node) => node.disconnect.mock.calls.length === 1)).toBe(
      true,
    );
    expect(engine.play().ok).toBe(false);
    engine.dispose();
  });
  it("stops and disconnects on timeout, replacement, disarm and disposal", async () => {
    vi.useFakeTimers();
    const { engine, oscillators, context } = fakeAudio();
    await engine.arm();
    engine.play({ durationSeconds: 0.5 });
    vi.advanceTimersByTime(550);
    expect(oscillators[0].disconnect).toHaveBeenCalledOnce();
    engine.play();
    engine.play();
    expect(oscillators[1].disconnect).toHaveBeenCalledOnce();
    engine.disarm();
    expect(oscillators[2].disconnect).toHaveBeenCalledOnce();
    expect(engine.play().ok).toBe(false);
    engine.dispose();
    expect(context.close).toHaveBeenCalledOnce();
    expect((await engine.arm()).ok).toBe(false);
  });
  it("stops when browser audio becomes suspended and requires explicit rearming", async () => {
    const { engine, context, oscillators } = fakeAudio();
    await engine.arm();
    engine.play();
    context.state = "suspended";
    context.onstatechange?.();
    expect(oscillators[0].disconnect).toHaveBeenCalledOnce();
    context.state = "running";
    expect(engine.play().ok).toBe(false);
    engine.dispose();
  });
  it("reports unavailable audio and cleans up partial tone creation failures", async () => {
    const unavailable = new BrowserAttentionSound(() => {
      throw new Error("Unsupported");
    });
    expect((await unavailable.arm()).ok).toBe(false);
    const { engine, context, oscillators } = fakeAudio();
    await engine.arm();
    context.createGain.mockImplementationOnce(() => {
      throw new Error("Device unavailable");
    });
    expect(engine.play().ok).toBe(false);
    expect(oscillators[0].disconnect).toHaveBeenCalledOnce();
    engine.dispose();
  });
  it("late resume cannot arm after disposal or activation timeout", async () => {
    vi.useFakeTimers();
    for (const dispose of [true, false]) {
      const { engine, context } = fakeAudio();
      let resume!: () => void;
      context.resume.mockImplementationOnce(
        () => new Promise<void>((resolve) => (resume = resolve)),
      );
      const activation = engine.arm();
      if (dispose) engine.dispose();
      else await vi.advanceTimersByTimeAsync(1500);
      context.state = "running";
      resume();
      expect((await activation).ok).toBe(false);
      expect(engine.play().ok).toBe(false);
      engine.dispose();
    }
  });
  it("bounds malformed volume independently from the UI", () => {
    expect(clampAttentionVolume(NaN)).toBe(0.35);
    expect(clampAttentionVolume(Infinity)).toBe(0.35);
    expect(clampAttentionVolume(-1)).toBe(0);
    expect(clampAttentionVolume(2)).toBe(1);
  });
});
