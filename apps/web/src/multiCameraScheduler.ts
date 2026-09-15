import { validateCameraArea } from "./cameraGrid";
import type { DetectionRect } from "./liveDetectionTypes";
import type { PresentedFrame } from "./monitoringContinuity";

export type ScheduledCamera = Readonly<{
  id: string;
  /** Full-source normalised coordinates, after explicit camera confirmation. */
  crop: Readonly<DetectionRect>;
}>;
export type CameraRunInput = {
  organisationId: string;
  branchId: string;
  sourceId: string;
  sourceWidth: number;
  sourceHeight: number;
  cameras: readonly ScheduledCamera[];
};
export type CameraRunContext = Readonly<
  Omit<CameraRunInput, "cameras"> & {
    epoch: number;
    cameras: readonly ScheduledCamera[];
  }
>;
export type ScheduledFrame = PresentedFrame & {
  sourceWidth: number;
  sourceHeight: number;
};
export type CameraWorkTicket = Readonly<{
  id: number;
  context: CameraRunContext;
  camera: ScheduledCamera;
  frame: Readonly<ScheduledFrame>;
  issuedAt: number;
  deadlineAt: number;
  signal: AbortSignal;
  /** True when this camera exceeded the configured revisit gap. */
  continuityBroken: boolean;
}>;
export type CameraStopReason =
  | "stopped"
  | "context_changed"
  | "clock_discontinuity"
  | "source_discontinuity";
type RejectionReason =
  | CameraStopReason
  | "cancelled"
  | "deadline_expired"
  | "stale_frame"
  | "invalid_frame"
  | "unknown_ticket";
export type CameraDispatch = {
  tickets: readonly CameraWorkTicket[];
  reason: RejectionReason | null;
};
export type CameraSettlement =
  | {
      status: "accepted";
      ticket: CameraWorkTicket;
      /** Includes inference time, measured immediately before consumption. */
      continuityBroken: boolean;
    }
  | { status: "failed" }
  | { status: "rejected"; reason: RejectionReason };
export type CameraSchedulerOptions = {
  /** No timer is installed. Use the same monotonic clock as observedAt. */
  now?: () => number;
  sampleIntervalMs?: number;
  maxInFlight?: number;
  maxFrameAgeMs?: number;
  maxResultAgeMs?: number;
  maxClockGapMs?: number;
  maxCameraRevisitMs?: number;
};

type CameraState = {
  camera: ScheduledCamera;
  lastSlot: number;
  lastSequence: number;
  blockedSlot: number | null;
  started: number;
  completed: number;
  failed: number;
  cancelled: number;
  rejected: number;
  capacityDeferrals: number;
  capacityMissedSamples: number;
  lastStartedAt: number | null;
  lastCompletedAt: number | null;
  revisitGaps: number[];
  continuityResets: number;
};
type Run = {
  context: CameraRunContext;
  cameras: CameraState[];
  startedAt: number;
  stoppedAt: number | null;
  stopReason: CameraStopReason | null;
  latest: Readonly<ScheduledFrame> | null;
  cursor: number;
};
type Pending = {
  ticket: CameraWorkTicket;
  camera: CameraState;
  controller: AbortController;
  cancellation: RejectionReason | null;
};

function bounded(value: number, minimum: number, maximum: number) {
  return Number.isFinite(value) && value >= minimum && value <= maximum;
}
function identifier(value: string) {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= 256 &&
    value.trim() === value &&
    !/[\u0000-\u001f\u007f]/.test(value)
  );
}
function copyInput(input: CameraRunInput) {
  if (
    !input ||
    !identifier(input.organisationId) ||
    !identifier(input.branchId) ||
    !identifier(input.sourceId) ||
    !Number.isInteger(input.sourceWidth) ||
    !Number.isInteger(input.sourceHeight) ||
    !bounded(input.sourceWidth, 1, 16384) ||
    !bounded(input.sourceHeight, 1, 16384) ||
    !Array.isArray(input.cameras) ||
    ![1, 4, 6].includes(input.cameras.length)
  )
    throw new Error(
      "Confirm a source, branch and one, four or six camera areas.",
    );
  const ids = new Set<string>();
  const cameras = input.cameras.map((camera) => {
    if (!camera || !identifier(camera.id) || ids.has(camera.id))
      throw new Error(
        "Each confirmed camera needs a distinct local identifier.",
      );
    ids.add(camera.id);
    return Object.freeze({
      id: camera.id,
      crop: Object.freeze(validateCameraArea(camera.crop)),
    });
  });
  for (let i = 0; i < cameras.length; i++) {
    for (let j = i + 1; j < cameras.length; j++) {
      const a = cameras[i].crop;
      const b = cameras[j].crop;
      if (
        Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x) >
          0.000001 &&
        Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y) > 0.000001
      )
        throw new Error("Confirmed camera areas must not overlap.");
    }
  }
  return {
    organisationId: input.organisationId,
    branchId: input.branchId,
    sourceId: input.sourceId,
    sourceWidth: input.sourceWidth,
    sourceHeight: input.sourceHeight,
    cameras: Object.freeze(cameras),
  };
}

/** Pull-based scheduling only: no media, inference, persistence or alarm side
 * effects. Cancellation reserves capacity until terminal acknowledgement. */
export class MultiCameraScheduler {
  private readonly now: () => number;
  private readonly interval: number;
  private readonly limit: number;
  private readonly frameAge: number;
  private readonly resultAge: number;
  private readonly clockGap: number;
  private readonly cameraRevisit: number;
  private run: Run | null = null;
  private epoch = 0;
  private nextId = 0;
  private lastNow: number | null = null;
  private readonly pending = new Map<number, Pending>();

  constructor(options: CameraSchedulerOptions = {}) {
    this.now = options.now ?? (() => performance.now());
    this.interval = options.sampleIntervalMs ?? 250;
    this.limit = options.maxInFlight ?? 1;
    this.frameAge = options.maxFrameAgeMs ?? 1000;
    this.resultAge = options.maxResultAgeMs ?? 1500;
    this.clockGap = options.maxClockGapMs ?? 2000;
    this.cameraRevisit = options.maxCameraRevisitMs ?? 1200;
    if (
      typeof this.now !== "function" ||
      !bounded(this.interval, 16, 60000) ||
      !Number.isInteger(this.limit) ||
      !bounded(this.limit, 1, 6) ||
      !bounded(this.frameAge, 16, 10000) ||
      !bounded(this.resultAge, 16, 60000) ||
      !bounded(this.clockGap, 16, 60000) ||
      !bounded(this.cameraRevisit, 100, 10000)
    )
      throw new Error("Choose bounded camera scheduling and freshness limits.");
  }

  /** Explicit rearming, even for identical IDs. Invalid input preserves the
   * current run; a valid restart cancels it before issuing the new epoch. */
  start(input: CameraRunInput): CameraRunContext {
    const copied = copyInput(input);
    const now = this.now();
    if (!bounded(now, 0, Number.MAX_SAFE_INTEGER)) {
      this.stop("clock_discontinuity");
      throw new Error("A valid monotonic clock is required to start cameras.");
    }
    if (this.epoch >= Number.MAX_SAFE_INTEGER)
      throw new Error(
        "Restart the application before starting another source.",
      );
    this.stop("context_changed");
    this.lastNow = now;
    const context = Object.freeze({ ...copied, epoch: ++this.epoch });
    this.run = {
      context,
      startedAt: now,
      stoppedAt: null,
      stopReason: null,
      latest: null,
      cursor: 0,
      cameras: context.cameras.map((camera) => ({
        camera,
        lastSlot: -1,
        lastSequence: -1,
        blockedSlot: null,
        started: 0,
        completed: 0,
        failed: 0,
        cancelled: 0,
        rejected: 0,
        capacityDeferrals: 0,
        capacityMissedSamples: 0,
        lastStartedAt: null,
        lastCompletedAt: null,
        revisitGaps: [],
        continuityResets: 0,
      })),
    };
    return context;
  }

  private abort(work: Pending, reason: RejectionReason) {
    if (work.cancellation !== null) return;
    work.cancellation = reason;
    work.camera.cancelled++;
    work.controller.abort(reason);
  }

  stop(reason: CameraStopReason = "stopped"): void {
    if (this.run && this.run.stoppedAt === null) {
      this.run.stoppedAt = this.lastNow ?? this.run.startedAt;
      this.run.stopReason = reason;
      for (const work of this.pending.values()) this.abort(work, reason);
    }
  }

  private time(): number | null {
    const now = this.now();
    if (
      !bounded(now, 0, Number.MAX_SAFE_INTEGER) ||
      (this.lastNow !== null &&
        (now < this.lastNow ||
          (this.run?.stoppedAt === null && now - this.lastNow > this.clockGap)))
    ) {
      this.stop("clock_discontinuity");
      return null;
    }
    this.lastNow = now;
    for (const work of this.pending.values())
      if (now >= work.ticket.deadlineAt) this.abort(work, "deadline_expired");
    return now;
  }

  private slot(run: Run, now: number) {
    const slot = Math.floor(
      ((run.stoppedAt ?? now) - run.startedAt) / this.interval,
    );
    for (const camera of run.cameras) {
      if (camera.blockedSlot !== null && camera.blockedSlot < slot) {
        camera.capacityMissedSamples++;
        camera.blockedSlot = null;
      }
    }
    return slot;
  }

  /** The original context object must be retained by capture callbacks. A
   * reconstructed object or a callback from an old source is not eligible. */
  dispatch(context: CameraRunContext, frame: ScheduledFrame): CameraDispatch {
    const now = this.time();
    const run = this.run;
    const reject = (reason: RejectionReason): CameraDispatch => ({
      tickets: [],
      reason,
    });
    if (!run || context !== run.context) return reject("context_changed");
    if (now === null || run.stoppedAt !== null)
      return reject(run.stopReason ?? "stopped");
    if (
      !frame ||
      !Number.isSafeInteger(frame.sequence) ||
      frame.sequence < 0 ||
      !bounded(frame.mediaTime, 0, Number.MAX_SAFE_INTEGER) ||
      !bounded(frame.observedAt, run.startedAt, now) ||
      !Number.isInteger(frame.sourceWidth) ||
      !Number.isInteger(frame.sourceHeight)
    )
      return reject("invalid_frame");
    const previous = run.latest;
    if (
      frame.sourceWidth !== context.sourceWidth ||
      frame.sourceHeight !== context.sourceHeight ||
      (previous &&
        (frame.sequence < previous.sequence ||
          frame.mediaTime < previous.mediaTime ||
          frame.observedAt < previous.observedAt ||
          Math.abs(
            (frame.mediaTime - previous.mediaTime) * 1000 -
              (frame.observedAt - previous.observedAt),
          ) > this.clockGap ||
          (frame.sequence === previous.sequence &&
            (frame.mediaTime !== previous.mediaTime ||
              frame.observedAt !== previous.observedAt))))
    ) {
      this.stop("source_discontinuity");
      return reject("source_discontinuity");
    }
    if (
      now - frame.observedAt > this.frameAge ||
      now >= frame.observedAt + this.resultAge
    )
      return reject("stale_frame");
    run.latest = Object.freeze({
      sequence: frame.sequence,
      mediaTime: frame.mediaTime,
      observedAt: frame.observedAt,
      sourceWidth: frame.sourceWidth,
      sourceHeight: frame.sourceHeight,
    });
    const slot = this.slot(run, now);
    const tickets: CameraWorkTicket[] = [];
    const first = run.cursor;
    for (let offset = 0; offset < run.cameras.length; offset++) {
      const index = (first + offset) % run.cameras.length;
      const camera = run.cameras[index];
      if (
        camera.lastSlot === slot ||
        camera.lastSequence === frame.sequence ||
        (camera.lastStartedAt !== null &&
          now - camera.lastStartedAt < this.interval)
      )
        continue;
      const busy = [...this.pending.values()].some(
        (work) => work.ticket.context === context && work.camera === camera,
      );
      if (this.pending.size >= this.limit || busy) {
        if (camera.blockedSlot !== slot) {
          camera.blockedSlot = slot;
          camera.capacityDeferrals++;
        }
        continue;
      }
      if (this.nextId >= Number.MAX_SAFE_INTEGER) {
        this.stop();
        throw new Error("Restart the application before scheduling more work.");
      }
      const controller = new AbortController();
      const continuityBroken =
        camera.lastCompletedAt !== null &&
        now - camera.lastCompletedAt > this.cameraRevisit;
      const ticket: CameraWorkTicket = Object.freeze({
        id: ++this.nextId,
        context,
        camera: camera.camera,
        frame: run.latest,
        issuedAt: now,
        deadlineAt: frame.observedAt + this.resultAge,
        signal: controller.signal,
        continuityBroken,
      });
      camera.lastSlot = slot;
      camera.lastSequence = frame.sequence;
      camera.started++;
      camera.lastStartedAt = now;
      camera.blockedSlot = null;
      this.pending.set(ticket.id, {
        ticket,
        camera,
        controller,
        cancellation: null,
      });
      run.cursor = (index + 1) % run.cameras.length;
      tickets.push(ticket);
    }
    return { tickets: Object.freeze(tickets), reason: null };
  }

  cancel(ticket: CameraWorkTicket): boolean {
    const work = this.pending.get(ticket.id);
    if (!work || work.ticket !== ticket) return false;
    this.abort(work, "cancelled");
    return true;
  }

  /** Call only once the work has ended, or its worker was terminated. An abort
   * request alone is not terminal. Only accepted results may proceed to review. */
  settle(
    ticket: CameraWorkTicket,
    outcome: "completed" | "failed" | "cancelled",
  ): CameraSettlement {
    const now = this.time();
    const work = this.pending.get(ticket.id);
    if (!work || work.ticket !== ticket)
      return { status: "rejected", reason: "unknown_ticket" };
    if (!["completed", "failed", "cancelled"].includes(outcome))
      throw new Error("A terminal work outcome is required.");
    this.pending.delete(ticket.id);
    let reason = work.cancellation;
    if (
      reason === null &&
      (this.run?.context !== ticket.context || this.run.stoppedAt !== null)
    )
      reason = "context_changed";
    if (
      reason === null &&
      (now === null ||
        !this.run?.latest ||
        now - this.run.latest.observedAt > this.frameAge)
    )
      reason = "stale_frame";
    if (reason === null && outcome === "cancelled") {
      this.abort(work, "cancelled");
      reason = "cancelled";
    }
    if (reason !== null) {
      work.camera.rejected++;
      return { status: "rejected", reason };
    }
    if (outcome === "failed") {
      work.camera.failed++;
      return { status: "failed" };
    }
    if (now === null)
      throw new Error("A completed ticket requires a valid clock.");
    work.camera.completed++;
    let continuityBroken = ticket.continuityBroken;
    if (work.camera.lastCompletedAt !== null) {
      const completedGap = now - work.camera.lastCompletedAt;
      work.camera.revisitGaps.push(completedGap);
      if (work.camera.revisitGaps.length > 32)
        work.camera.revisitGaps.splice(0, work.camera.revisitGaps.length - 32);
      // Dispatch-time checks cannot see slow inference. The settlement result
      // is the authoritative gate used immediately before an engine update.
      continuityBroken ||= completedGap > this.cameraRevisit;
    }
    if (continuityBroken) work.camera.continuityResets++;
    work.camera.lastCompletedAt = now;
    return { status: "accepted", ticket, continuityBroken };
  }

  /** Epoch check only. After asynchronous work also recheck result deadline
   * and source freshness. This is never server authorisation or alarm arming. */
  isCurrent(context: CameraRunContext): boolean {
    const now = this.time();
    return (
      now !== null &&
      this.run?.context === context &&
      this.run.stoppedAt === null
    );
  }

  snapshot() {
    const now = this.time();
    const run = this.run;
    const at = now ?? this.lastNow ?? 0;
    const slot = run ? this.slot(run, at) : -1;
    const cameras =
      run?.cameras.map((camera) => {
        const expectedSamples = slot + 1;
        const scheduledInClosedSlots =
          camera.started - Number(camera.lastSlot === slot);
        const orderedGaps = [...camera.revisitGaps].sort((a, b) => a - b);
        return {
          id: camera.camera.id,
          expectedSamples,
          started: camera.started,
          completed: camera.completed,
          failed: camera.failed,
          cancelled: camera.cancelled,
          rejected: camera.rejected,
          missedSamples: Math.max(0, slot - scheduledInClosedSlots),
          capacityDeferrals: camera.capacityDeferrals,
          capacityMissedSamples: camera.capacityMissedSamples,
          schedulingCoverage: camera.started / expectedSamples,
          completionCoverage: camera.completed / expectedSamples,
          lastStartedAt: camera.lastStartedAt,
          lastCompletedAt: camera.lastCompletedAt,
          completionAgeMs:
            camera.lastCompletedAt === null
              ? null
              : at - camera.lastCompletedAt,
          revisitP95Ms:
            orderedGaps.length === 0
              ? null
              : orderedGaps[Math.ceil(orderedGaps.length * 0.95) - 1],
          continuityResets: camera.continuityResets,
          pending: [...this.pending.values()].filter(
            (work) =>
              work.ticket.context === run.context && work.camera === camera,
          ).length,
        };
      }) ?? [];
    return {
      context: run?.context ?? null,
      active: !!run && run.stoppedAt === null,
      stopReason: run?.stopReason ?? null,
      maxInFlight: this.limit,
      inFlight: this.pending.size,
      awaitingCancellation: [...this.pending.values()].filter(
        (work) => work.cancellation !== null,
      ).length,
      saturated: this.pending.size >= this.limit,
      sourceFresh:
        !!run?.latest &&
        run.stoppedAt === null &&
        at - run.latest.observedAt <= this.frameAge,
      /** Ratios count work opportunities, never people/actions detected. */
      coverageKind: "processing_opportunities" as const,
      cameras,
    };
  }
}
