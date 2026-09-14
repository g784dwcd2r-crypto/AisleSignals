import {
  cameraContextKey,
  validateCameraContext,
  type CameraContext,
} from "./cameraContext";
import type { LiveSourceKind } from "./liveDetectionTypes";
import {
  InteractionFrameBuffer,
  type SampledFrame,
} from "./interactionCapture";

export type AllCameraRun = Readonly<{
  runId: string;
  generation: number;
  sourceKey: string;
  sourceKind: LiveSourceKind;
  sourceLabel: string;
  runtime: string;
  cameras: readonly CameraContext[];
}>;
export const cameraKey = cameraContextKey;
export const cameraName = (camera: CameraContext) =>
  `Camera ${camera.camera_index + 1} · ${camera.layout} grid`;
export type ProductTicket = {
  readonly run: AllCameraRun;
  readonly camera: CameraContext;
  readonly frames: readonly SampledFrame[];
  readonly startedAt: number;
  cancelled: boolean;
};
type CameraState = {
  camera: CameraContext;
  buffer: InteractionFrameBuffer;
  lastAttempt: number;
  lastStart: number;
  cadence: number | null;
  lastEnd: number;
  started: number;
  completed: number;
  failed: number;
  late: number;
  error: string;
  lastCompleted: number | null;
  interval: [number, number] | null;
};
/** Integrated sampler/job stage. There is one outstanding ticket even across stop/start. */
export class MultiCameraInteractions {
  private run: AllCameraRun | null = null;
  private cameras: CameraState[] = [];
  private startedAt = 0;
  private stoppedAt: number | null = null;
  private captureCursor = 0;
  private jobCursor = 0;
  private active: ProductTicket | null = null;
  private lastGlobalStart = -Infinity;
  start(run: AllCameraRun, now: number) {
    this.stop(now);
    if (
      ![4, 6].includes(run.cameras.length) ||
      new Set(run.cameras.map(cameraKey)).size !== run.cameras.length
    )
      throw new Error("Confirm four or six distinct cameras before starting.");
    const first = run.cameras[0];
    run.cameras.forEach((camera, index) => {
      validateCameraContext(camera);
      if (
        camera.camera_index !== index ||
        camera.layout !== first.layout ||
        camera.source_id !== first.source_id ||
        camera.epoch !== first.epoch ||
        camera.source_width !== first.source_width ||
        camera.source_height !== first.source_height ||
        run.cameras.length !== (camera.layout === "2x2" ? 4 : 6)
      )
        throw new Error(
          "Confirm the complete ordered camera set for one source and epoch.",
        );
    });
    this.run = run;
    this.startedAt = now;
    this.stoppedAt = null;
    this.captureCursor = this.jobCursor = 0;
    this.cameras = run.cameras.map((camera) => ({
      camera,
      buffer: new InteractionFrameBuffer(),
      lastAttempt: -Infinity,
      lastStart: -Infinity,
      cadence: null,
      lastEnd: -Infinity,
      started: 0,
      completed: 0,
      failed: 0,
      late: 0,
      error: "",
      lastCompleted: null,
      interval: null,
    }));
  }
  stop(now: number) {
    if (this.stoppedAt === null) this.stoppedAt = now;
    this.run = null;
    this.cameras.forEach((camera) => camera.buffer.reset());
    if (this.active) this.active.cancelled = true;
  }
  observe(
    media: number,
    now: number,
    capture: (camera: CameraContext) => SampledFrame,
  ) {
    if (!this.run) return;
    const observations = this.cameras.map((camera) =>
      camera.buffer.observe(media, now),
    );
    if (observations.includes("reset")) {
      this.stop(now);
      throw new Error(
        "Camera sampling continuity changed. Start detection again.",
      );
    }
    // Observe every camera each heartbeat, but stagger at most two JPEG encodes.
    let encoded = 0;
    const first = this.captureCursor;
    for (
      let offset = 0;
      offset < this.cameras.length && encoded < 2;
      offset++
    ) {
      const index = (first + offset) % this.cameras.length;
      const state = this.cameras[index];
      if (observations[index] !== "capture" || now - state.lastAttempt < 1250)
        continue;
      state.lastAttempt = now;
      encoded++;
      this.captureCursor = (index + 1) % this.cameras.length;
      try {
        state.buffer.add(capture(state.camera));
        state.error = "";
      } catch (failure) {
        state.buffer.reset();
        state.error =
          failure instanceof Error
            ? failure.message
            : "Camera sample unavailable.";
      }
    }
  }
  reserve(now: number): ProductTicket | null {
    if (!this.run || this.active || now - this.lastGlobalStart < 2000)
      return null;
    for (let offset = 0; offset < this.cameras.length; offset++) {
      const index = (this.jobCursor + offset) % this.cameras.length;
      const state = this.cameras[index],
        frames = state.buffer.sequence(now);
      if (
        state.error ||
        now - state.lastStart < 10000 ||
        frames.length !== 4 ||
        frames[3].at_seconds <= state.lastEnd
      )
        continue;
      state.cadence = Number.isFinite(state.lastStart)
        ? now - state.lastStart
        : null;
      state.lastStart = now;
      state.lastEnd = frames[3].at_seconds;
      state.started++;
      this.lastGlobalStart = now;
      this.jobCursor = (index + 1) % this.cameras.length;
      const ticket = {
        run: this.run,
        camera: state.camera,
        frames: Object.freeze(
          frames.map((frame) => Object.freeze({ ...frame })),
        ),
        startedAt: now,
        cancelled: false,
      };
      this.active = ticket;
      return ticket;
    }
    return null;
  }
  settle(
    ticket: ProductTicket,
    outcome: "completed" | "failed" | "cancelled",
    now: number,
    error = "",
  ) {
    if (this.active !== ticket) return false;
    this.active = null;
    if (ticket.cancelled || this.run !== ticket.run || outcome === "cancelled")
      return false;
    const state = this.cameras.find((item) => item.camera === ticket.camera)!;
    if (outcome === "completed") {
      state.completed++;
      state.lastCompleted = now;
      state.interval = [
        ticket.frames[0].at_seconds,
        ticket.frames[3].at_seconds,
      ];
      if (now - ticket.frames[3].capturedAt > 15000) state.late++;
    } else {
      state.failed++;
      state.error = error;
    }
    return true;
  }
  snapshot(now: number) {
    const end = this.stoppedAt ?? now;
    const expected = Math.floor(Math.max(0, end - this.startedAt) / 10000) + 1;
    return this.cameras.map((state) => ({
      camera: state.camera,
      frames: this.run ? state.buffer.snapshot(now) : [],
      pending: this.active?.camera === state.camera && !this.active.cancelled,
      started: state.started,
      completed: state.completed,
      failed: state.failed,
      late: state.late,
      missed: Math.max(
        0,
        expected -
          1 -
          (state.started -
            Number(state.lastStart >= this.startedAt + (expected - 1) * 10000)),
      ),
      expected,
      lastCompletedAge:
        state.lastCompleted === null
          ? null
          : Math.max(0, end - state.lastCompleted),
      interval: state.interval,
      cadence: state.cadence,
      error: state.error,
      nextIn: Math.max(0, 10000 - (now - state.lastStart)),
    }));
  }
}
