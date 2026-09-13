import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { watchVideoContinuity } from "./monitoringContinuity";

function videoFixture() {
  let id = 0;
  const callbacks = new Map<number, VideoFrameRequestCallback>();
  const cancelled: number[] = [];
  const video = {
    currentTime: 0,
    requestVideoFrameCallback(callback: VideoFrameRequestCallback) {
      callbacks.set(++id, callback);
      return id;
    },
    cancelVideoFrameCallback(callbackId: number) {
      cancelled.push(callbackId);
      callbacks.delete(callbackId);
    },
  } as unknown as HTMLVideoElement;
  return {
    video,
    callbacks,
    cancelled,
    present(mediaTime: number, presentedFrames: number) {
      const [callbackId, callback] = [...callbacks.entries()][0];
      callbacks.delete(callbackId);
      callback(performance.now(), {
        mediaTime,
        presentedFrames,
      } as VideoFrameCallbackMetadata);
    },
  };
}

describe("video presentation continuity", () => {
  beforeEach(() => {
    vi.useFakeTimers({
      toFake: ["setInterval", "clearInterval", "Date", "performance"],
    });
  });
  afterEach(() => vi.useRealTimers());

  it("does not call media clock progress a fresh frame", async () => {
    const fixture = videoFixture();
    const fault = vi.fn();
    const watcher = watchVideoContinuity(fixture.video, fault);
    fixture.video.currentTime = 80;
    expect(watcher.latest()).toBeNull();
    await vi.advanceTimersByTimeAsync(3500);
    expect(fault).toHaveBeenCalledExactlyOnceWith("frames_stalled");
    await expect(watcher.firstFrame).rejects.toThrow("monitoring stopped");
    expect(fixture.callbacks.size).toBe(0);
  });

  it("accepts fresh presentations of an unchanged scene without reading pixels", async () => {
    const fixture = videoFixture();
    const fault = vi.fn();
    const watcher = watchVideoContinuity(fixture.video, fault);
    for (let frame = 0; frame < 30; frame++) {
      await vi.advanceTimersByTimeAsync(250);
      fixture.present(frame / 4, frame + 1);
    }
    await watcher.firstFrame;
    expect(watcher.latest()).toEqual({
      sequence: 30,
      mediaTime: 7.25,
      observedAt: 7500,
    });
    expect(fault).not.toHaveBeenCalled();
    watcher.close();
  });

  it("rejects repeated presentation counters rather than extending freshness", async () => {
    const fixture = videoFixture();
    const fault = vi.fn();
    const watcher = watchVideoContinuity(fixture.video, fault);
    fixture.present(0, 1);
    await watcher.firstFrame;
    for (let frame = 1; frame < 14; frame++) {
      await vi.advanceTimersByTimeAsync(250);
      fixture.present(frame / 4, 1);
    }
    await vi.advanceTimersByTimeAsync(250);
    expect(fault).toHaveBeenCalledExactlyOnceWith("frames_stalled");
    expect(watcher.latest()).toBeNull();
  });

  it.each([-1, 8])(
    "stops on a media discontinuity of %s seconds",
    async (mediaTime) => {
      const fixture = videoFixture();
      const fault = vi.fn();
      const watcher = watchVideoContinuity(fixture.video, fault);
      fixture.present(1, 1);
      await watcher.firstFrame;
      await vi.advanceTimersByTimeAsync(250);
      fixture.present(mediaTime, 2);
      expect(fault).toHaveBeenCalledExactlyOnceWith("media_jump");
      expect(watcher.latest()).toBeNull();
    },
  );

  it("catches sleep even when the monotonic clock did not advance", async () => {
    const fixture = videoFixture();
    const fault = vi.fn();
    const watcher = watchVideoContinuity(fixture.video, fault);
    fixture.present(0, 1);
    await watcher.firstFrame;
    expect(watcher.isFresh(1000)).toBe(true);
    vi.setSystemTime(Date.now() + 60000);
    // An alarm completion can run before the watchdog on resume. Its read-only
    // session check must already reject the old context.
    expect(watcher.isFresh(1000)).toBe(false);
    await vi.advanceTimersByTimeAsync(250);
    expect(fault).toHaveBeenCalledExactlyOnceWith("clock_gap");
  });

  it("rejects stale context before the frame-gap timeout releases capture", async () => {
    const fixture = videoFixture();
    const fault = vi.fn();
    const watcher = watchVideoContinuity(fixture.video, fault);
    fixture.present(0, 1);
    await watcher.firstFrame;
    await vi.advanceTimersByTimeAsync(1100);
    expect(watcher.isFresh(1000)).toBe(false);
    expect(fault).not.toHaveBeenCalled();
    fixture.present(1.1, 2);
    expect(watcher.isFresh(1000)).toBe(true);
    watcher.close();
    expect(watcher.isFresh(1000)).toBe(false);
  });

  it("catches an event-loop gap before accepting a newly resumed frame", async () => {
    const fixture = videoFixture();
    const fault = vi.fn();
    const watcher = watchVideoContinuity(fixture.video, fault);
    fixture.present(0, 1);
    await watcher.firstFrame;
    vi.setSystemTime(Date.now() + 5000);
    fixture.present(0.1, 2);
    expect(fault).toHaveBeenCalledExactlyOnceWith("clock_gap");
    expect(watcher.latest()).toBeNull();
  });

  it("cancels pending callbacks and ignores already queued delivery after close", async () => {
    const fixture = videoFixture();
    const fault = vi.fn();
    const watcher = watchVideoContinuity(fixture.video, fault);
    const lateCallback = [...fixture.callbacks.values()][0];
    watcher.close();
    lateCallback(performance.now(), {
      mediaTime: 1,
      presentedFrames: 1,
    } as VideoFrameCallbackMetadata);
    await vi.advanceTimersByTimeAsync(10000);
    await expect(watcher.firstFrame).rejects.toThrow("monitoring stopped");
    expect(watcher.latest()).toBeNull();
    expect(fault).not.toHaveBeenCalled();
    expect(fixture.callbacks.size).toBe(0);
    expect(fixture.cancelled).toEqual([1]);
  });

  it("does not let invalid presentation metadata activate monitoring", async () => {
    const fixture = videoFixture();
    const fault = vi.fn();
    const watcher = watchVideoContinuity(fixture.video, fault);
    fixture.present(NaN, 1);
    fixture.present(0, Infinity);
    await vi.advanceTimersByTimeAsync(3500);
    await expect(watcher.firstFrame).rejects.toThrow();
    expect(fault).toHaveBeenCalledExactlyOnceWith("frames_stalled");
  });

  it("fails explicitly when presentation callbacks are unsupported", () => {
    expect(() => watchVideoContinuity({} as HTMLVideoElement, vi.fn())).toThrow(
      "cannot verify fresh video frames",
    );
  });
});
