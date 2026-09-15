import { describe, expect, it } from "vitest";
import { cameraGridTiles } from "./cameraGrid";
import {
  MultiCameraScheduler,
  type CameraRunInput,
  type CameraSchedulerOptions,
  type CameraWorkTicket,
  type ScheduledFrame,
} from "./multiCameraScheduler";

function input(count: 1 | 4 | 6 = 4): CameraRunInput {
  return {
    organisationId: "synthetic-group",
    branchId: "synthetic-branch",
    sourceId: "synthetic-screen",
    sourceWidth: 1920,
    sourceHeight: 1080,
    cameras: cameraGridTiles(
      count === 1 ? "single" : count === 4 ? "2x2" : "3x2",
    ),
  };
}
function rig(count: 1 | 4 | 6 = 4, options: CameraSchedulerOptions = {}) {
  let now = 0;
  const scheduler = new MultiCameraScheduler({
    now: () => now,
    sampleIntervalMs: 100,
    maxFrameAgeMs: 500,
    maxResultAgeMs: 1000,
    maxClockGapMs: 2000,
    ...options,
  });
  const context = scheduler.start(input(count));
  const frame = (sequence = now + 1): ScheduledFrame => ({
    sequence,
    observedAt: now,
    mediaTime: now / 1000,
    sourceWidth: 1920,
    sourceHeight: 1080,
  });
  return {
    scheduler,
    context,
    frame,
    at: (value: number) => {
      now = value;
    },
  };
}

function one(tickets: readonly CameraWorkTicket[]) {
  expect(tickets).toHaveLength(1);
  return tickets[0];
}

describe("bounded multi-camera work scheduling", () => {
  it("services six cameras in bounded parallel pairs without a starvation gap", () => {
    const r = rig(6, { maxInFlight: 2 });
    const seen = new Map<string, number[]>();
    for (let batch = 0; batch < 6; batch++) {
      r.at(batch * 100);
      const tickets = r.scheduler.dispatch(r.context, r.frame()).tickets;
      expect(tickets).toHaveLength(2);
      for (const ticket of tickets) {
        const times = seen.get(ticket.camera.id) ?? [];
        times.push(batch * 100);
        seen.set(ticket.camera.id, times);
        r.scheduler.settle(ticket, "completed");
      }
    }
    expect(seen.size).toBe(6);
    for (const times of seen.values())
      expect(times.length === 2 && times[1] - times[0] <= 300).toBe(true);
    expect(
      r.scheduler
        .snapshot()
        .cameras.every(
          (camera) =>
            camera.revisitP95Ms === 300 && camera.continuityResets === 0,
        ),
    ).toBe(true);
  });

  it("marks a camera ticket when its measured revisit gap broke continuity", () => {
    const r = rig(1, { maxCameraRevisitMs: 500 });
    const first = one(r.scheduler.dispatch(r.context, r.frame()).tickets);
    r.scheduler.settle(first, "completed");
    r.at(600);
    const resumed = one(r.scheduler.dispatch(r.context, r.frame()).tickets);
    expect(resumed.continuityBroken).toBe(true);
    r.scheduler.settle(resumed, "completed");
    expect(r.scheduler.snapshot().cameras[0]).toMatchObject({
      revisitP95Ms: 600,
      continuityResets: 1,
    });
  });

  it("fails continuity at settlement when inference creates the revisit gap", () => {
    const r = rig(1, {
      maxCameraRevisitMs: 500,
      maxResultAgeMs: 1_000,
    });
    const first = one(r.scheduler.dispatch(r.context, r.frame()).tickets);
    expect(r.scheduler.settle(first, "completed")).toMatchObject({
      status: "accepted",
      continuityBroken: false,
    });
    r.at(100);
    const slow = one(r.scheduler.dispatch(r.context, r.frame()).tickets);
    expect(slow.continuityBroken).toBe(false);
    // A fresh presented frame keeps the source healthy while this camera's
    // bounded inference job is still running.
    r.at(700);
    expect(r.scheduler.dispatch(r.context, r.frame()).tickets).toEqual([]);
    expect(r.scheduler.settle(slow, "completed")).toMatchObject({
      status: "accepted",
      continuityBroken: true,
    });
    expect(r.scheduler.snapshot().cameras[0]).toMatchObject({
      revisitP95Ms: 700,
      continuityResets: 1,
    });
  });

  it.each([1, 4, 6] as const)(
    "fairly services %i cameras under sustained one-slot load",
    (count) => {
      const r = rig(count);
      const order: string[] = [];
      for (let i = 0; i < count * 3; i++) {
        r.at(i * 100);
        const ticket = one(r.scheduler.dispatch(r.context, r.frame()).tickets);
        order.push(ticket.camera.id);
        expect(r.scheduler.settle(ticket, "completed").status).toBe("accepted");
      }
      expect(order).toEqual(
        Array.from({ length: 3 }, () =>
          r.context.cameras.map((c) => c.id),
        ).flat(),
      );
      expect(r.scheduler.snapshot().cameras.map((c) => c.started)).toEqual(
        Array(count).fill(3),
      );
    },
  );

  it("bounds global concurrency, with one active job per camera and no queue", () => {
    const r = rig(6, { maxInFlight: 2 });
    const first = r.scheduler.dispatch(r.context, r.frame()).tickets;
    expect(first.map((t) => t.camera.id)).toEqual(
      r.context.cameras.slice(0, 2).map((c) => c.id),
    );
    r.at(100);
    expect(r.scheduler.dispatch(r.context, r.frame()).tickets).toEqual([]);
    expect(r.scheduler.snapshot().inFlight).toBe(2);
    r.scheduler.settle(first[0], "completed");
    const next = one(r.scheduler.dispatch(r.context, r.frame()).tickets);
    expect(next.camera.id).toBe(r.context.cameras[2].id);
    expect(r.scheduler.snapshot().inFlight).toBe(2);
  });

  it("uses remaining capacity for other cameras while one camera remains busy", () => {
    const r = rig(4, { maxInFlight: 4 });
    const first = r.scheduler.dispatch(r.context, r.frame()).tickets;
    for (const ticket of first.slice(1))
      r.scheduler.settle(ticket, "completed");
    r.at(100);
    const second = r.scheduler.dispatch(r.context, r.frame()).tickets;
    expect(second).toHaveLength(3);
    expect(second.some((t) => t.camera.id === first[0].camera.id)).toBe(false);
    expect(r.scheduler.snapshot().cameras[0].pending).toBe(1);
  });

  it("never catches up missed slots by queuing old frame work", () => {
    const r = rig(4, { maxInFlight: 4 });
    r.at(900);
    const work = r.scheduler.dispatch(r.context, r.frame()).tickets;
    expect(work).toHaveLength(4);
    expect(work.every((t) => t.frame.observedAt === 900)).toBe(true);
    expect(r.scheduler.snapshot().cameras.map((c) => c.missedSamples)).toEqual([
      9, 9, 9, 9,
    ]);
    for (const ticket of work) r.scheduler.settle(ticket, "completed");
    expect(r.scheduler.dispatch(r.context, r.frame()).tickets).toEqual([]);
  });

  it("requires a new frame and a new sampling slot for each camera", () => {
    const r = rig(1);
    const original = r.frame(1);
    r.scheduler.settle(
      one(r.scheduler.dispatch(r.context, original).tickets),
      "completed",
    );
    r.at(50);
    expect(r.scheduler.dispatch(r.context, r.frame(2)).tickets).toEqual([]);
    r.at(100);
    const sameSequence = { ...r.frame(2), observedAt: 50, mediaTime: 0.05 };
    const second = one(r.scheduler.dispatch(r.context, sameSequence).tickets);
    r.scheduler.settle(second, "completed");
    r.at(200);
    expect(r.scheduler.dispatch(r.context, sameSequence).tickets).toEqual([]);
    expect(r.scheduler.dispatch(r.context, r.frame(3)).tickets).toHaveLength(1);
  });
});

describe("sampling boundaries", () => {
  it("does not burst at opposite sides of a sampling-slot boundary", () => {
    const r = rig(1);
    r.at(99);
    r.scheduler.settle(
      one(r.scheduler.dispatch(r.context, r.frame()).tickets),
      "completed",
    );
    r.at(100);
    expect(r.scheduler.dispatch(r.context, r.frame()).tickets).toHaveLength(0);
    r.at(199);
    expect(r.scheduler.dispatch(r.context, r.frame()).tickets).toHaveLength(1);
  });

  it("stops on a reported forward seek rather than joining unrelated moments", () => {
    const r = rig(1);
    const ticket = one(r.scheduler.dispatch(r.context, r.frame()).tickets);
    r.at(100);
    expect(
      r.scheduler.dispatch(r.context, { ...r.frame(), mediaTime: 10 }).reason,
    ).toBe("source_discontinuity");
    expect(r.scheduler.settle(ticket, "completed").status).toBe("rejected");
  });
});

describe("immutable camera and authority context", () => {
  it("clones and deeply freezes only the expected context and frame fields", () => {
    const r = rig(1);
    const original = input(1);
    const context = r.scheduler.start(original);
    const frame = { ...r.frame(), unexpectedPayload: { footage: true } };
    const ticket = one(r.scheduler.dispatch(context, frame).tickets);
    original.branchId = "changed";
    original.cameras = cameraGridTiles("2x2");
    frame.observedAt = 900;
    expect(ticket.context.branchId).toBe("synthetic-branch");
    expect(ticket.context.cameras).toHaveLength(1);
    expect(ticket.frame.observedAt).toBe(0);
    expect(ticket.frame).not.toHaveProperty("unexpectedPayload");
    for (const frozen of [
      ticket,
      ticket.frame,
      context,
      context.cameras,
      context.cameras[0],
      context.cameras[0].crop,
    ])
      expect(Object.isFrozen(frozen)).toBe(true);
  });

  it.each(["organisationId", "branchId", "sourceId"] as const)(
    "invalidates old work when %s changes",
    (field) => {
      const r = rig(1);
      const old = one(r.scheduler.dispatch(r.context, r.frame()).tickets);
      const next = r.scheduler.start({ ...input(1), [field]: "replacement" });
      expect(next.epoch).toBe(r.context.epoch + 1);
      expect(old.signal.aborted).toBe(true);
      expect(r.scheduler.dispatch(r.context, r.frame()).reason).toBe(
        "context_changed",
      );
      expect(r.scheduler.settle(old, "completed")).toEqual({
        status: "rejected",
        reason: "context_changed",
      });
      const fresh = one(r.scheduler.dispatch(next, r.frame()).tickets);
      expect(fresh.context[field]).toBe("replacement");
    },
  );

  it("requires a fresh epoch even when the same source identifiers are rearmed", () => {
    const r = rig(1);
    const old = one(r.scheduler.dispatch(r.context, r.frame()).tickets);
    const next = r.scheduler.start(input(1));
    expect(r.scheduler.isCurrent(r.context)).toBe(false);
    expect(r.scheduler.isCurrent(next)).toBe(true);
    expect(r.scheduler.settle(old, "completed").status).toBe("rejected");
  });

  it("keeps exact measured crops in each ticket and revokes them on crop changes", () => {
    const r = rig(1);
    const selected = {
      ...input(1),
      cameras: [
        { id: "selected", crop: { x: 0.2, y: 0.1, width: 0.6, height: 0.7 } },
      ],
    };
    const context = r.scheduler.start(selected);
    const old = one(r.scheduler.dispatch(context, r.frame()).tickets);
    selected.cameras[0].crop.x = 0.21;
    expect(old.camera.crop.x).toBe(0.2);
    const next = r.scheduler.start(selected);
    expect(old.signal.aborted).toBe(true);
    expect(next.cameras[0].crop.x).toBe(0.21);
    expect(r.scheduler.settle(old, "completed").status).toBe("rejected");
  });

  it("rejects reconstructed contexts and forged tickets without freeing owned capacity", () => {
    const r = rig(1);
    expect(r.scheduler.dispatch({ ...r.context }, r.frame()).reason).toBe(
      "context_changed",
    );
    const ticket = one(r.scheduler.dispatch(r.context, r.frame()).tickets);
    expect(r.scheduler.settle({ ...ticket }, "completed")).toEqual({
      status: "rejected",
      reason: "unknown_ticket",
    });
    expect(r.scheduler.cancel({ ...ticket })).toBe(false);
    expect(r.scheduler.snapshot().inFlight).toBe(1);
    expect(r.scheduler.settle(ticket, "completed").status).toBe("accepted");
    expect(r.scheduler.settle(ticket, "completed").status).toBe("rejected");
  });

  it("preserves the current run if a replacement configuration is invalid", () => {
    const r = rig(1);
    const pending = one(r.scheduler.dispatch(r.context, r.frame()).tickets);
    expect(() => r.scheduler.start({ ...input(1), branchId: "" })).toThrow();
    expect(pending.signal.aborted).toBe(false);
    expect(r.scheduler.isCurrent(r.context)).toBe(true);
  });
});

describe("cancellation, result freshness and failure", () => {
  it("reserves capacity through stop and restart until cancellation is acknowledged", () => {
    const r = rig(1);
    const old = one(r.scheduler.dispatch(r.context, r.frame()).tickets);
    r.scheduler.stop();
    const context = r.scheduler.start(input(1));
    expect(r.scheduler.dispatch(context, r.frame()).tickets).toEqual([]);
    expect(r.scheduler.snapshot()).toMatchObject({
      inFlight: 1,
      awaitingCancellation: 1,
    });
    expect(r.scheduler.settle(old, "cancelled").status).toBe("rejected");
    expect(r.scheduler.dispatch(context, r.frame()).tickets).toHaveLength(1);
  });

  it("keeps hung cancelled jobs bounded across repeated rearming", () => {
    const r = rig(1);
    const old = one(r.scheduler.dispatch(r.context, r.frame()).tickets);
    for (let i = 0; i < 20; i++) {
      const next = r.scheduler.start(input(1));
      expect(r.scheduler.dispatch(next, r.frame()).tickets).toHaveLength(0);
      expect(r.scheduler.snapshot().inFlight).toBe(1);
    }
    r.scheduler.settle(old, "cancelled");
    expect(r.scheduler.snapshot().inFlight).toBe(0);
  });

  it("cancels one camera once without stopping other camera work", () => {
    const r = rig(4, { maxInFlight: 2 });
    const [cancelled, other] = r.scheduler.dispatch(
      r.context,
      r.frame(),
    ).tickets;
    expect(r.scheduler.cancel(cancelled)).toBe(true);
    r.scheduler.cancel(cancelled);
    expect(r.scheduler.snapshot().cameras[0].cancelled).toBe(1);
    expect(r.scheduler.settle(cancelled, "completed")).toEqual({
      status: "rejected",
      reason: "cancelled",
    });
    expect(r.scheduler.settle(other, "completed").status).toBe("accepted");
  });

  it("expires a job at its deadline without pretending the worker has stopped", () => {
    const r = rig(1, { maxResultAgeMs: 200 });
    const ticket = one(r.scheduler.dispatch(r.context, r.frame()).tickets);
    r.at(200);
    expect(r.scheduler.snapshot()).toMatchObject({
      inFlight: 1,
      awaitingCancellation: 1,
    });
    expect(ticket.signal.reason).toBe("deadline_expired");
    expect(r.scheduler.dispatch(r.context, r.frame()).tickets).toHaveLength(0);
    expect(r.scheduler.settle(ticket, "completed")).toEqual({
      status: "rejected",
      reason: "deadline_expired",
    });
    expect(r.scheduler.dispatch(r.context, r.frame()).tickets).toHaveLength(1);
  });

  it("rejects completion when the latest presented frame is stale", () => {
    const r = rig(1, { maxFrameAgeMs: 100 });
    const ticket = one(r.scheduler.dispatch(r.context, r.frame()).tickets);
    r.at(101);
    expect(r.scheduler.settle(ticket, "completed")).toEqual({
      status: "rejected",
      reason: "stale_frame",
    });
    expect(r.scheduler.snapshot().cameras[0].completed).toBe(0);
  });

  it("allows bounded work while fresh frames continue arriving", () => {
    const r = rig(1, { maxFrameAgeMs: 100 });
    const ticket = one(r.scheduler.dispatch(r.context, r.frame()).tickets);
    r.at(300);
    expect(r.scheduler.dispatch(r.context, r.frame()).tickets).toHaveLength(0);
    expect(r.scheduler.settle(ticket, "completed").status).toBe("accepted");
  });

  it("releases failed or worker-cancelled work without accepting a result", () => {
    const r = rig(4, { maxInFlight: 2 });
    const [failed, cancelled] = r.scheduler.dispatch(
      r.context,
      r.frame(),
    ).tickets;
    expect(r.scheduler.settle(failed, "failed")).toEqual({ status: "failed" });
    expect(r.scheduler.settle(cancelled, "cancelled")).toEqual({
      status: "rejected",
      reason: "cancelled",
    });
    expect(r.scheduler.snapshot().inFlight).toBe(0);
    expect(r.scheduler.snapshot().cameras.map((c) => c.completed)).toEqual([
      0, 0, 0, 0,
    ]);
  });

  it.each([NaN, -1, 2001])(
    "stops on discontinuous clock value %s and requires explicit restart",
    (value) => {
      const r = rig(1);
      const ticket = one(r.scheduler.dispatch(r.context, r.frame()).tickets);
      r.at(value);
      expect(r.scheduler.snapshot()).toMatchObject({
        active: false,
        stopReason: "clock_discontinuity",
      });
      expect(ticket.signal.aborted).toBe(true);
      r.at(50);
      expect(r.scheduler.dispatch(r.context, r.frame()).reason).toBe(
        "clock_discontinuity",
      );
      expect(r.scheduler.settle(ticket, "completed").status).toBe("rejected");
      expect(r.scheduler.isCurrent(r.scheduler.start(input(1)))).toBe(true);
    },
  );

  it.each([
    { sourceWidth: 1280 },
    { sourceHeight: 720 },
    { sequence: 0 },
    { sequence: 1, mediaTime: 0.1 },
    { sequence: 1, observedAt: 1 },
  ])("requires explicit rearming after source discontinuity %j", (changes) => {
    const r = rig(1);
    const ticket = one(r.scheduler.dispatch(r.context, r.frame(1)).tickets);
    r.at(10);
    expect(
      r.scheduler.dispatch(r.context, { ...ticket.frame, ...changes }).reason,
    ).toBe("source_discontinuity");
    expect(ticket.signal.aborted).toBe(true);
    expect(r.scheduler.settle(ticket, "completed").status).toBe("rejected");
  });

  it("rejects future, malformed and already-expired source frames without scheduling", () => {
    const r = rig(1, { maxFrameAgeMs: 500, maxResultAgeMs: 100 });
    expect(
      r.scheduler.dispatch(r.context, { ...r.frame(), observedAt: 1 }).reason,
    ).toBe("invalid_frame");
    expect(
      r.scheduler.dispatch(r.context, { ...r.frame(), mediaTime: NaN }).reason,
    ).toBe("invalid_frame");
    expect(
      r.scheduler.dispatch(r.context, { ...r.frame(), sequence: 1.5 }).reason,
    ).toBe("invalid_frame");
    const previous = r.frame();
    r.at(100);
    expect(r.scheduler.dispatch(r.context, previous).reason).toBe(
      "stale_frame",
    );
    expect(r.scheduler.snapshot().inFlight).toBe(0);
  });
});

describe("processing coverage accounting", () => {
  it("counts capacity deferrals once per slot and separates them from missed opportunities", () => {
    const r = rig(4);
    const first = one(r.scheduler.dispatch(r.context, r.frame()).tickets);
    for (let i = 0; i < 20; i++) r.scheduler.dispatch(r.context, r.frame());
    expect(
      r.scheduler.snapshot().cameras.map((c) => c.capacityDeferrals),
    ).toEqual([0, 1, 1, 1]);
    r.scheduler.settle(first, "completed");
    const second = one(r.scheduler.dispatch(r.context, r.frame()).tickets);
    r.scheduler.settle(second, "completed");
    r.at(100);
    const snap = r.scheduler.snapshot();
    expect(snap.cameras.map((c) => c.missedSamples)).toEqual([0, 0, 1, 1]);
    expect(snap.cameras.map((c) => c.capacityMissedSamples)).toEqual([
      0, 0, 1, 1,
    ]);
    expect(snap.cameras.map((c) => c.completionCoverage)).toEqual([
      0.5, 0.5, 0, 0,
    ]);
    expect(snap.coverageKind).toBe("processing_opportunities");
  });

  it("does not attribute missing input or an unpolled interval to measured overload", () => {
    const r = rig(1);
    r.at(500);
    expect(r.scheduler.snapshot().cameras[0]).toMatchObject({
      expectedSamples: 6,
      missedSamples: 5,
      capacityDeferrals: 0,
      capacityMissedSamples: 0,
      completionAgeMs: null,
      completionCoverage: 0,
    });
    expect(r.scheduler.snapshot().sourceFresh).toBe(false);
  });

  it("freezes opportunity counts after stopping and resets coverage on a new epoch", () => {
    const r = rig(1);
    r.at(200);
    r.scheduler.snapshot();
    r.scheduler.stop();
    r.at(1000);
    expect(r.scheduler.snapshot().cameras[0].expectedSamples).toBe(3);
    r.scheduler.start(input(1));
    expect(r.scheduler.snapshot().cameras[0]).toMatchObject({
      expectedSamples: 1,
      missedSamples: 0,
      started: 0,
    });
  });
});

describe("bounded configuration", () => {
  it.each([
    { maxInFlight: 0 },
    { maxInFlight: 7 },
    { maxInFlight: 1.5 },
    { sampleIntervalMs: 0 },
    { sampleIntervalMs: Infinity },
    { maxFrameAgeMs: 0 },
    { maxResultAgeMs: -1 },
    { maxClockGapMs: NaN },
    { maxCameraRevisitMs: 99 },
  ])("refuses invalid scheduling options %j", (options) => {
    expect(() => new MultiCameraScheduler(options)).toThrow();
  });

  it("rejects unsupported counts, duplicate IDs, overlapping or invalid crops", () => {
    const r = rig(1);
    const cases = [
      { ...input(4), cameras: input(4).cameras.slice(0, 2) },
      { ...input(4), cameras: Array(4).fill(input(1).cameras[0]) },
      {
        ...input(4),
        cameras: input(4).cameras.map((c) => ({
          ...c,
          crop: input(1).cameras[0].crop,
        })),
      },
      { ...input(1), sourceWidth: 0 },
      {
        ...input(1),
        cameras: [
          { id: "bad", crop: { x: -0.1, y: 0, width: 0.5, height: 0.5 } },
        ],
      },
    ];
    for (const invalid of cases)
      expect(() => r.scheduler.start(invalid)).toThrow();
  });
});
