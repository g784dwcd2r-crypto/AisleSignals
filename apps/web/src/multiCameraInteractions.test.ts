import { describe, expect, it } from "vitest";
import {
  MultiCameraInteractions,
  type AllCameraRun,
  cameraKey,
} from "./multiCameraInteractions";
import { mapCameraGridToArea } from "./cameraGrid";
import { validateCameraContext } from "./cameraContext";
import type { SampledFrame } from "./interactionCapture";
function run(count: 4 | 6 = 4, epoch = 1): AllCameraRun {
  const layout = count === 4 ? "2x2" : "3x2";
  return Object.freeze({
    runId: `run-${epoch}`,
    generation: epoch,
    sourceKey: `source-${epoch}`,
    runtime: "synthetic-runtime",
    sourceKind: "CAMERA",
    sourceLabel: "Synthetic grid",
    cameras: Object.freeze(
      mapCameraGridToArea(layout, { x: 0, y: 0, width: 1, height: 1 }).map(
        (tile) =>
          validateCameraContext({
            source_id: "00000000-0000-0000-0000-000000000001",
            epoch,
            layout,
            camera_index: tile.index,
            source_width: 960,
            source_height: 600,
            crop: tile.crop,
          }),
      ),
    ),
  });
}
function advance(
  controller: MultiCameraInteractions,
  from: number,
  to: number,
) {
  for (let now = from; now <= to; now += 100)
    controller.observe(now / 1000, now, (camera) => ({
      capturedAt: now,
      at_seconds: now / 1000,
      jpeg_base64: `camera-${camera.camera_index}-at-${now}`,
      width: 320,
      height: 300,
      sourceWidth: 320,
      sourceHeight: 300,
    }));
}
describe("integrated all-camera product stage", () => {
  it.each([4, 6] as const)(
    "collects independent staggered buffers for %i cameras while one model request is held",
    (count) => {
      const controller = new MultiCameraInteractions();
      controller.start(run(count), 0);
      advance(controller, 0, 4500);
      const ticket = controller.reserve(4500)!;
      expect(ticket.camera.camera_index).toBe(0);
      const frozen = ticket.frames.map((frame) => frame.jpeg_base64);
      advance(controller, 4600, 14500);
      expect(controller.reserve(14500)).toBeNull();
      expect(ticket.frames.map((frame) => frame.jpeg_base64)).toEqual(frozen);
      for (const row of controller.snapshot(14500)) {
        expect(row.frames).toHaveLength(4);
        expect(
          row.frames.every((frame) =>
            frame.jpeg_base64.startsWith(`camera-${row.camera.camera_index}-`),
          ),
        ).toBe(true);
        expect(new Set(row.frames.map((frame) => frame.at_seconds)).size).toBe(
          4,
        );
      }
    },
  );
  it.each([4, 6] as const)(
    "rotates %i cameras fairly without violating global/per-camera start bounds",
    (count) => {
      const controller = new MultiCameraInteractions();
      controller.start(run(count), 0);
      const observed: { camera: number; at: number }[] = [];
      for (let now = 0; now <= 30000; now += 100) {
        advance(controller, now, now);
        const ticket = controller.reserve(now);
        if (ticket) {
          observed.push({ camera: ticket.camera.camera_index, at: now });
          controller.settle(ticket, "completed", now + 1);
        }
      }
      expect(observed.slice(0, count).map((item) => item.camera)).toEqual(
        Array.from({ length: count }, (_, index) => index),
      );
      for (const row of controller.snapshot(30000))
        expect(row.cadence).toBeGreaterThanOrEqual(10000);
      observed.forEach((item, index) => {
        if (index)
          expect(item.at - observed[index - 1].at).toBeGreaterThanOrEqual(2000);
        const previous = observed
          .slice(0, index)
          .reverse()
          .find((row) => row.camera === item.camera);
        if (previous)
          expect(item.at - previous.at).toBeGreaterThanOrEqual(10000);
      });
    },
  );
  it("retains cancelled capacity across new epochs, rejects stale/forged completion and then resumes only the new run", () => {
    const controller = new MultiCameraInteractions();
    controller.start(run(), 0);
    advance(controller, 0, 4500);
    const ticket = controller.reserve(4500)!;
    controller.start(run(4, 2), 4600);
    advance(controller, 4600, 9000);
    expect(ticket.cancelled).toBe(true);
    expect(controller.reserve(9000)).toBeNull();
    expect(controller.settle({ ...ticket }, "completed", 9000)).toBe(false);
    expect(controller.reserve(9000)).toBeNull();
    expect(controller.settle(ticket, "completed", 9000)).toBe(false);
    const next = controller.reserve(9000)!;
    expect(next.run.generation).toBe(2);
    expect(next.frames.every((frame) => frame.capturedAt >= 4600)).toBe(true);
  });
  it("clears all samples on media discontinuity instead of joining an old and new camera moment", () => {
    const controller = new MultiCameraInteractions();
    controller.start(run(), 0);
    advance(controller, 0, 4500);
    expect(() =>
      controller.observe(1, 4600, () => {
        throw new Error("Must not capture");
      }),
    ).toThrow("continuity");
    expect(controller.reserve(4600)).toBeNull();
    expect(
      controller.snapshot(4600).every((row) => row.frames.length === 0),
    ).toBe(true);
  });
  it("does not let one failed camera sample block fresh healthy cameras", () => {
    const controller = new MultiCameraInteractions();
    controller.start(run(), 0);
    for (let now = 0; now < 5000; now += 100)
      controller.observe(now / 1000, now, (camera) => {
        if (camera.camera_index === 0)
          throw new Error("Synthetic crop unreadable");
        return {
          capturedAt: now,
          at_seconds: now / 1000,
          jpeg_base64: `${camera.camera_index}:${now}`,
          width: 320,
          height: 300,
          sourceWidth: 320,
          sourceHeight: 300,
        } satisfies SampledFrame;
      });
    expect(controller.reserve(5000)?.camera.camera_index).toBe(1);
    expect(controller.snapshot(5000)[0].error).toContain("unreadable");
  });
  it("counts delayed completions and missed opportunities without labelling buffered frames as inference", () => {
    const controller = new MultiCameraInteractions();
    controller.start(run(), 0);
    advance(controller, 0, 4500);
    const ticket = controller.reserve(4500)!;
    advance(controller, 4600, 22000);
    expect(controller.settle(ticket, "completed", 22000)).toBe(true);
    const rows = controller.snapshot(22000);
    expect(rows[0]).toMatchObject({
      completed: 1,
      late: 1,
      expected: 3,
      missed: 1,
    });
    expect(rows[1]).toMatchObject({ started: 0, completed: 0, missed: 2 });
  });
  it("rejects incomplete, repeated-index and mixed-source camera sets", () => {
    const original = run();
    for (const cameras of [
      original.cameras.slice(0, 3),
      original.cameras.map((camera, i) =>
        i === 2 ? { ...camera, camera_index: 1 } : camera,
      ),
      original.cameras.map((camera, i) =>
        i === 2 ? { ...camera, epoch: 7 } : camera,
      ),
    ]) {
      const controller = new MultiCameraInteractions();
      expect(() => controller.start({ ...original, cameras }, 0)).toThrow();
      expect(controller.reserve(4500)).toBeNull();
    }
  });
  it("binds identity to geometry, not just the camera label", () => {
    const camera = run().cameras[0];
    expect(cameraKey(camera)).not.toBe(
      cameraKey({ ...camera, crop: { ...camera.crop, width: 0.4 } }),
    );
  });
});
