import { test, expect, type Page } from "@playwright/test";
import { resolve } from "node:path";
import { CONTINUITY_LIMITS } from "../../apps/web/src/monitoringContinuity";

async function open(page: Page) {
  await page.addInitScript(() => {
    const probe = ((window as any).__monitoring = {
      holdFrames: false,
      holdInference: false,
      frames: [] as (() => void)[],
      results: [] as (() => void)[],
      calls: 0,
      lastInferenceAt: 0,
      presentedFrames: 0,
      lastPresentedAt: 0,
      audio: 0,
      tracks: [] as MediaStreamTrack[],
    });
    // Deliberate model-boundary fixture: these tests verify lifecycle, not AI accuracy.
    class EmptyPoseWorker {
      onmessage: ((event: any) => void) | null = null;
      onerror = null;
      postMessage(request: any) {
        request.bitmap?.close();
        const deliver = () =>
          this.onmessage?.({
            data:
              request.type === "init"
                ? { type: "ready" }
                : { type: "result", id: request.id, persons: [] },
          });
        if (request.type === "frame") {
          probe.calls++;
          probe.lastInferenceAt = performance.now();
          if (probe.holdInference) {
            probe.results.push(deliver);
            return;
          }
        }
        queueMicrotask(deliver);
      }
      terminate() {}
    }
    (window as any).Worker = EmptyPoseWorker;
    const present = HTMLVideoElement.prototype.requestVideoFrameCallback;
    HTMLVideoElement.prototype.requestVideoFrameCallback = function (callback) {
      return present.call(this, (now, metadata) => {
        const deliver = () => {
          if (metadata.presentedFrames > probe.presentedFrames) {
            probe.presentedFrames = metadata.presentedFrames;
            probe.lastPresentedAt = performance.now();
          }
          callback(now, metadata);
        };
        if (probe.holdFrames) probe.frames.push(deliver);
        else deliver();
      });
    };
    const start = OscillatorNode.prototype.start;
    OscillatorNode.prototype.start = function (...args) {
      probe.audio++;
      return start.apply(this, args);
    };
    navigator.mediaDevices.getUserMedia = async () => {
      const canvas = document.createElement("canvas");
      canvas.width = 640;
      canvas.height = 360;
      const context = canvas.getContext("2d")!;
      const draw = () => {
        context.fillStyle = "#7d9679";
        context.fillRect(0, 0, canvas.width, canvas.height);
      };
      draw();
      const stream = canvas.captureStream(10);
      probe.tracks = stream.getTracks();
      // Identical pixels, fresh browser frames: no movement does not mean offline.
      const timer = setInterval(() => {
        if (stream.getVideoTracks()[0].readyState === "ended")
          clearInterval(timer);
        else draw();
      }, 100);
      return stream;
    };
  });
  await page.goto("/");
  await page.getByLabel(/^Email/).fill("manager@harbour.demo");
  await page.getByLabel("Password", { exact: true }).fill("AisleDemo!2026");
  await page.getByRole("button", { name: "Open demo workspace" }).click();
  await page
    .getByRole("button", { name: "LIVE DETECTION", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Live Detection", exact: true }),
  ).toBeVisible();
}

test("fresh static camera frames remain monitored; track mute releases capture and requires reconnect", async ({
  page,
}) => {
  await open(page);
  await expect(
    page.getByLabel("Sound on movement-rule alerts"),
  ).not.toBeChecked();
  await page
    .getByRole("button", { name: "Connect camera", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Start detection", exact: true }),
  ).toBeEnabled();
  await page
    .getByRole("button", { name: "Start detection", exact: true })
    .click();
  await expect
    .poll(() => page.evaluate(() => (window as any).__monitoring.calls))
    .toBeGreaterThan(0);
  const baseline = await page.evaluate(() => ({
    startedAt: performance.now(),
    presentedFrames: (window as any).__monitoring.presentedFrames,
  }));
  // Observe real fresh presentations and inference beyond the stall deadline.
  // Counting 16 inferences within 5 seconds accidentally required near-maximum
  // processing throughput, although a slower fresh static source is healthy.
  const windowMs = CONTINUITY_LIMITS.frameGapMs + CONTINUITY_LIMITS.watchdogMs;
  await expect
    .poll(
      () =>
        page.evaluate(
          ({ baseline, windowMs }) => {
            const probe = (window as any).__monitoring;
            return (
              probe.presentedFrames > baseline.presentedFrames &&
              probe.lastPresentedAt - baseline.startedAt >= windowMs &&
              probe.lastInferenceAt - baseline.startedAt >= windowMs
            );
          },
          { baseline, windowMs },
        ),
      { intervals: [100] },
    )
    .toBe(true);
  await expect(
    page.getByText("POSE TRACKING RUNNING", { exact: true }),
  ).toBeVisible();
  await page.evaluate(() =>
    (window as any).__monitoring.tracks[0].dispatchEvent(new Event("mute")),
  );
  await expect(
    page.getByText("DETECTION STOPPED", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toContainText(
    "camera or shared window is unavailable",
  );
  await expect(
    page.getByRole("button", { name: "Start detection", exact: true }),
  ).toBeDisabled();
  expect(
    await page.evaluate(() =>
      (window as any).__monitoring.tracks.every(
        (track: MediaStreamTrack) => track.readyState === "ended",
      ),
    ),
  ).toBe(true);
  expect(await page.evaluate(() => (window as any).__monitoring.audio)).toBe(0);
});

test("an advancing playback clock cannot mask missing presented frames; late callback stays cancelled", async ({
  page,
}) => {
  await open(page);
  await page
    .getByLabel("Choose CCTV recording")
    .setInputFiles(resolve("tests/fixtures/synthetic-video.webm"));
  await page
    .getByRole("button", { name: "Start detection", exact: true })
    .click();
  await expect(
    page.getByText("POSE TRACKING RUNNING", { exact: true }),
  ).toBeVisible();
  const before = await page
    .locator("video")
    .evaluate((video) => video.currentTime);
  await page.evaluate(() => {
    (window as any).__monitoring.holdFrames = true;
  });
  await expect
    .poll(() => page.locator("video").evaluate((video) => video.currentTime))
    .toBeGreaterThan(before + 1);
  await expect(
    page.getByText(
      "Video frames stopped arriving. Reconnect or restart playback.",
      { exact: true },
    ),
  ).toBeVisible({ timeout: 6000 });
  await expect(
    page.getByText("DETECTION STOPPED", { exact: true }),
  ).toBeVisible();
  const calls = await page.evaluate(() => {
    const probe = (window as any).__monitoring;
    const calls = probe.calls;
    probe.frames.splice(0).forEach((deliver: () => void) => deliver());
    return calls;
  });
  expect(await page.evaluate(() => (window as any).__monitoring.calls)).toBe(
    calls,
  );
  await expect(
    page.getByRole("button", { name: "Start detection", exact: true }),
  ).toBeEnabled();
});

test("stopping during inference ignores a late result and clears reported processing rate", async ({
  page,
}) => {
  await open(page);
  await page.evaluate(() => {
    (window as any).__monitoring.holdInference = true;
  });
  await page
    .getByLabel("Choose CCTV recording")
    .setInputFiles(resolve("tests/fixtures/synthetic-video.webm"));
  await page
    .getByRole("button", { name: "Start detection", exact: true })
    .click();
  await expect
    .poll(() =>
      page.evaluate(() => (window as any).__monitoring.results.length),
    )
    .toBe(1);
  await page
    .getByRole("button", { name: "Stop detection", exact: true })
    .click();
  await page.evaluate(() =>
    (window as any).__monitoring.results
      .splice(0)
      .forEach((deliver: () => void) => deliver()),
  );
  await expect(
    page.getByText("DETECTION STOPPED", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".ld-metrics")).toContainText("0.0 fps");
  await expect(page.getByLabel("People currently tracked")).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__monitoring.audio)).toBe(0);
});

test("page hiding stops the current capture and fresh callbacks do not silently resume it", async ({
  page,
}) => {
  await open(page);
  await page
    .getByRole("button", { name: "Connect camera", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Start detection", exact: true })
    .click();
  await expect(
    page.getByText("POSE TRACKING RUNNING", { exact: true }),
  ).toBeVisible();
  // Controlled Page Lifecycle event; actual OS sleep remains a site acceptance check.
  await page.evaluate(() => window.dispatchEvent(new Event("pagehide")));
  await expect(
    page.getByText("Detection stopped because the page closed.", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Start detection", exact: true }),
  ).toBeDisabled();
  expect(
    await page.evaluate(() =>
      (window as any).__monitoring.tracks.every(
        (track: MediaStreamTrack) => track.readyState === "ended",
      ),
    ),
  ).toBe(true);
});
