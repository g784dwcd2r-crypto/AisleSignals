import { test as base, expect, type Page } from "@playwright/test";
import { spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";

// Each browser journey owns a fresh real pilot API/SQLite/evidence directory.
// Only the vision provider is mocked; no customer preview or model is contacted.
const server = String.raw`
import json, os, socket, sys, time
from pathlib import Path
for key in list(os.environ):
    if key.startswith('AISLESIGNALS_'): del os.environ[key]
listener=socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen(128)
port=listener.getsockname()[1]
unavailable=socket.socket(); unavailable.bind(('127.0.0.1',0))
os.environ['AISLESIGNALS_MODE']='pilot'
os.environ['AISLESIGNALS_PORT']=str(port)
os.environ['AISLESIGNALS_DB_PATH']=str(Path(sys.argv[1]).resolve()/'pilot.db')
os.environ['AISLESIGNALS_WEB_DIST']=str(Path.cwd()/'apps/web/dist')
os.environ['AISLESIGNALS_VISION_URL']='http://127.0.0.1:'+str(unavailable.getsockname()[1])
from services.api.app import app
from services.api.pilot_identity import initialise, add_site, grant_user
sys.path.insert(0,str(Path.cwd()/'tests/api'))
from test_interactions import MockProvider
initial=initialise(app.state.store,'Synthetic Case Group','Synthetic North','case.manager@example.test','Synthetic Case Manager','Synthetic case test passphrase 847!')
other=add_site(app.state.store, initial['site']['organisation_id'],'Synthetic South')
grant_user(app.state.store,'case.manager@example.test',other['id'],'MANAGER')
class DelayedReviewProvider(MockProvider):
    def analyze(self, frames):
        # Exercise real async completion beyond the short scanner fixture's end.
        time.sleep(float((Path(sys.argv[1])/'delay').read_text()) if (Path(sys.argv[1])/'delay').exists() else 0.15)
        if (Path(sys.argv[1])/'fail').exists():
            raise RuntimeError('synthetic provider unavailable')
        return super().analyze(frames)
app.state.interactions.provider=DelayedReviewProvider()
import uvicorn
print('CASE_READY '+json.dumps({'url':'http://127.0.0.1:'+str(port),'north':initial['site']['id'],'south':other['id']}),flush=True)
uvicorn.Server(uvicorn.Config(app,log_level='warning',access_log=False,proxy_headers=False)).run(sockets=[listener])
`;
type Installation = {
  url: string;
  north: string;
  south: string;
  directory: string;
};
const test = base.extend<{ installation: Installation }>({
  installation: async ({}, use) => {
    const directory = await mkdtemp(
      join(tmpdir(), "aislesignals-case-browser-"),
    );
    const child = spawn(
      process.env.AISLESIGNALS_TEST_PYTHON || "python",
      ["-u", "-c", server, directory],
      { cwd: process.cwd(), stdio: ["ignore", "pipe", "pipe"] },
    );
    const closed = new Promise<void>((resolve) =>
      child.once("close", () => resolve()),
    );
    let output = "",
      errors = "",
      timer: NodeJS.Timeout | undefined;
    try {
      const ready = new Promise<Installation>((resolve, reject) => {
        child.stdout.on("data", (chunk) => {
          output += String(chunk);
          const line = output
            .split("\n")
            .find((line) => line.startsWith("CASE_READY "));
          if (line) resolve(JSON.parse(line.slice(11)));
        });
        child.stderr.on("data", (chunk) => {
          errors += String(chunk);
        });
        child.once("error", reject);
        child.once("exit", (code) =>
          reject(new Error(`Case fixture exited ${code}: ${errors}`)),
        );
      });
      const installation = await Promise.race([
        ready,
        new Promise<never>((_, reject) => {
          timer = setTimeout(
            () => reject(new Error("Case fixture startup timed out")),
            20000,
          );
        }),
      ]).finally(() => clearTimeout(timer));
      await expect
        .poll(async () => {
          try {
            return (await fetch(`${installation.url}/api/health`)).status;
          } catch {
            return 0;
          }
        })
        .toBe(200);
      await use({ ...installation, directory });
    } finally {
      if (child.exitCode === null) child.kill("SIGTERM");
      await closed;
      await rm(directory, { recursive: true, force: true });
    }
  },
});

async function openGrid(
  page: Page,
  installation: Installation,
  layout: "2x2" | "3x2" | "2x3",
) {
  await page.addInitScript(
    ({ layout }) => {
      const state = ((window as any).__all = {
        modes: [] as string[],
        workers: 0,
        cameraCount: layout === "2x2" ? 4 : 6,
        pose: [] as any[],
        occludedCamera: null as number | null,
        occludedFrames: [] as number[],
        sound: 0,
        soundAttempts: 0,
        ended: false,
      });
      class SilentNode {
        gain = {
          value: 0,
          setValueAtTime() {},
          linearRampToValueAtTime() {},
          exponentialRampToValueAtTime() {},
        };
        frequency = {
          value: 0,
          setValueAtTime() {},
          linearRampToValueAtTime() {},
        };
        connect() {
          return this;
        }
        disconnect() {}
        start() {
          state.soundAttempts++;
          if (state.failSound) throw new Error("synthetic audio failure");
          state.sound++;
        }
        stop() {}
      }
      class SilentAudio {
        state = "running";
        currentTime = 0;
        destination = {};
        createGain() {
          return new SilentNode();
        }
        createOscillator() {
          return new SilentNode();
        }
        async resume() {}
        async close() {
          this.state = "closed";
        }
      }
      (window as any).AudioContext = SilentAudio;
      class PoseWorker {
        onmessage: ((event: any) => void) | null = null;
        closed = false;
        constructor() {
          state.workers++;
        }
        postMessage(request: any) {
          if (request.type === "init") {
            state.modes.push(request.mode);
            queueMicrotask(() => this.onmessage?.({ data: { type: "ready" } }));
            return;
          }
          const sample = new OffscreenCanvas(
              request.bitmap.width,
              request.bitmap.height,
            ),
            context = sample.getContext("2d")!;
          context.drawImage(request.bitmap, 0, 0);
          const pixels = context.getImageData(
              Math.floor(sample.width / 2) - 8,
              Math.floor(sample.height / 2) - 8,
              16,
              16,
            ).data,
            rgb = [0, 0, 0];
          for (let i = 0; i < pixels.length; i += 4)
            for (let c = 0; c < 3; c++) rgb[c] += pixels[i + c] / 256;
          state.pose.push({ width: sample.width, height: sample.height, rgb });
          if (state.pose.length > 60) state.pose.shift();
          // The sixth synthetic tile is cyan. Select the mock's gap from the
          // actual cropped pixels, never from a production camera ID or label.
          const occluded =
            state.occludedCamera === 6 &&
            rgb[1] - rgb[0] > 45 &&
            rgb[2] - rgb[0] > 45;
          if (occluded) state.occludedFrames.push(request.timestampMs);
          request.bitmap.close();
          const points = Array.from({ length: 33 }, () => ({
            x: 0.5,
            y: 0.2,
            visibility: 0.99,
          }));
          for (const [i, x, y] of [
            [11, 0.42, 0.32],
            [12, 0.58, 0.32],
            [13, 0.39, 0.48],
            [14, 0.61, 0.48],
            [15, 0.38, 0.82],
            [16, 0.62, 0.82],
            [23, 0.44, 0.6],
            [24, 0.56, 0.6],
            [25, 0.43, 0.77],
            [26, 0.57, 0.77],
            [27, 0.42, 0.95],
            [28, 0.58, 0.95],
          ])
            points[i] = { x, y, visibility: 0.99 };
          setTimeout(() => {
            if (!this.closed)
              this.onmessage?.(
                {
                  data: {
                    type: "result",
                    id: request.id,
                    poses: occluded ? [] : [points],
                  },
                },
                state.delay ?? 0,
              );
          }, state.delay ?? 0);
        }
        terminate() {
          if (!this.closed) {
            state.workers--;
            this.closed = true;
          }
        }
      }
      (window as any).Worker = PoseWorker;
      navigator.mediaDevices.getUserMedia = async () => {
        const canvas = document.createElement("canvas");
        canvas.width = 600;
        canvas.height = 400;
        const context = canvas.getContext("2d")!;
        const columns = layout === "3x2" ? 3 : 2,
          rows = layout === "2x3" ? 3 : 2;
        const xs = columns === 3 ? [0, 207, 394, 600] : [0, 306, 600],
          ys = rows === 3 ? [0, 137, 265, 400] : [0, 204, 400];
        const colours = [
            [65, 0, 0],
            [0, 65, 0],
            [0, 0, 65],
            [65, 65, 0],
            [65, 0, 65],
            [0, 65, 65],
          ],
          data = context.createImageData(600, 400);
        for (let y = 0; y < 400; y++)
          for (let x = 0; x < 600; x++) {
            let col = 0,
              row = 0;
            while (col < columns - 1 && x >= xs[col + 1]) col++;
            while (row < rows - 1 && y >= ys[row + 1]) row++;
            const v =
                55 +
                ((x * 7 +
                  y * 11 +
                  Math.floor(x / 17) * 23 +
                  Math.floor(y / 13) * 19) %
                  120),
              gutter =
                xs.slice(1, -1).some((line) => Math.abs(x - line) <= 2) ||
                ys.slice(1, -1).some((line) => Math.abs(y - line) <= 2);
            data.data.set(
              [
                ...(gutter
                  ? [12, 12, 12]
                  : colours[row * columns + col].map((c) =>
                      Math.min(245, v + c),
                    )),
                255,
              ],
              (y * 600 + x) * 4,
            );
          }
        context.putImageData(data, 0, 0);
        const stream = canvas.captureStream(20);
        const timer = setInterval(() => {
          if (state.ended) {
            clearInterval(timer);
            return;
          }
          context.putImageData(data, 0, 0);
        }, 50);
        state.disconnect = () => {
          state.ended = true;
          for (const track of stream.getTracks()) {
            track.stop();
            track.dispatchEvent(new Event("ended"));
          }
        };
        state.resize = () => {
          canvas.width = 602;
        };
        return stream;
      };
    },
    { layout },
  );
  await page.goto(installation.url);
  await page
    .getByLabel("Email address", { exact: true })
    .fill("case.manager@example.test");
  await page
    .getByLabel("Password", { exact: true })
    .fill("Synthetic case test passphrase 847!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page
    .getByLabel("Active pharmacy branch")
    .selectOption(installation.north);
  await keepRuntimeHealthy(page, installation);
  const menu = page.getByRole("button", {
    name: "Open navigation",
    exact: true,
  });
  if (await menu.isVisible()) await menu.click();
  await page
    .getByRole("button", { name: "LIVE DETECTION", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Connect camera", exact: true })
    .click();
  await expect(
    page.getByRole("combobox", { name: "Camera layout", exact: true }),
  ).toHaveValue(layout);
  await page
    .getByRole("button", { name: "Use this layout", exact: true })
    .click();
  await page
    .getByRole("radio", {
      name: `All ${layout === "2x2" ? 4 : 6} confirmed cameras`,
      exact: true,
    })
    .check();
}
async function begin(page: Page, automatic = true) {
  for (const label of [
    "Entrance zone is visible",
    "Exit zone is visible",
    "Cashier zone is visible",
    "Relevant shelf zones are visible",
  ])
    await page.getByLabel(label, { exact: true }).check();
  await page
    .getByLabel("Enable all-camera product analysis", { exact: true })
    .check();
  if (automatic)
    await page
      .getByLabel("Analyse all cameras automatically", { exact: true })
      .check();
  await page.evaluate(() => {
    // Record initial UI identities when they actually appear. A camera-local
    // gap can correctly retire Person #1 while later evidence is reviewed.
    const state = (window as any).__all;
    state.firstTracks = {};
    const observer = new MutationObserver(() => {
      for (const label of document.querySelectorAll(".ld-track > span")) {
        const text = label.textContent ?? "",
          camera = /^Camera ([1-6]) · Person #\d+$/.exec(text)?.[1];
        if (camera) state.firstTracks[camera] ??= text;
      }
      if (Object.keys(state.firstTracks).length === state.cameraCount)
        observer.disconnect();
    });
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
      characterData: true,
    });
  });
  await page
    .getByRole("button", { name: "Start detection", exact: true })
    .click();
  await expect(
    page.getByText("POSE TRACKING RUNNING", { exact: true }),
  ).toBeVisible();
}
async function keepRuntimeHealthy(page: Page, installation: Installation) {
  const health = await (
    await page.request.get(`${installation.url}/api/runtime/health`)
  ).json();
  let healthRouted!: () => void;
  const firstRoutedHealth = new Promise<void>((resolve) => {
    healthRouted = resolve;
  });
  await page.route("**/api/runtime/health", async (route) => {
    healthRouted();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(health),
    });
  });
  await firstRoutedHealth;
}
async function pixels(page: Page, base64: string) {
  return page.evaluate(async (data) => {
    const img = new Image();
    img.src = `data:image/jpeg;base64,${data}`;
    await img.decode();
    const bitmap = await createImageBitmap(img),
      canvas = new OffscreenCanvas(bitmap.width, bitmap.height),
      context = canvas.getContext("2d")!;
    context.drawImage(bitmap, 0, 0);
    const pixels = context.getImageData(
        Math.floor(bitmap.width / 2) - 8,
        Math.floor(bitmap.height / 2) - 8,
        16,
        16,
      ).data,
      rgb = [0, 0, 0];
    for (let i = 0; i < pixels.length; i += 4)
      for (let c = 0; c < 3; c++) rgb[c] += pixels[i + c] / 256;
    const result = { width: bitmap.width, height: bitmap.height, rgb };
    bitmap.close();
    return result;
  }, base64);
}
function checkColour(rgb: number[], index: number) {
  const bright = [[0], [1], [2], [0, 1], [0, 2], [1, 2]][index];
  for (const channel of bright)
    for (const dim of [0, 1, 2].filter((c) => !bright.includes(c)))
      expect(rgb[channel] - rgb[dim]).toBeGreaterThan(45);
}
for (const layout of ["2x2", "3x2", "2x3"] as const)
  test(`all ${layout} cameras submit independent real crops and retain review provenance`, async ({
    page,
    installation,
  }) => {
    test.setTimeout(90000);
    const posts: any[] = [];
    const completed = new Set<string>();
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("request", (r) => {
      if (r.method() === "POST" && r.url().endsWith("/api/interactions/jobs"))
        posts.push({ body: r.postDataJSON(), at: Date.now() });
    });
    page.on("response", async (response) => {
      if (
        response.request().method() !== "GET" ||
        !/\/api\/interactions\/jobs\/[a-f0-9-]+$/.test(response.url()) ||
        !response.ok()
      )
        return;
      const job = await response.json().catch(() => null);
      if (job?.status === "completed" && job.result?.id)
        completed.add(job.result.id);
    });
    await openGrid(page, installation, layout);
    if (layout === "2x2") {
      await page
        .getByLabel("Alert on restricted-zone entry", { exact: true })
        .check();
      for (const label of ["Left %", "Top %"])
        await page.getByLabel(label, { exact: true }).fill("0");
      for (const label of ["Width %", "Height %"])
        await page.getByLabel(label, { exact: true }).fill("100");
    }
    await begin(page);
    const count = layout === "2x2" ? 4 : 6;
    // A POST means processing started, not that its observation is available.
    // Wait on the real completion boundary with room for a cold Windows API.
    await expect
      .poll(() => completed.size, { timeout: 50000 })
      .toBeGreaterThanOrEqual(count);
    expect(posts.length).toBeGreaterThanOrEqual(count);
    await expect(page.locator(".interaction-result")).toHaveCount(count, {
      timeout: 10000,
    });
    await page
      .getByLabel("Analyse all cameras automatically", { exact: true })
      .uncheck();
    expect(
      posts.slice(0, count).map((p) => p.body.camera_context.camera_index),
    ).toEqual(Array.from({ length: count }, (_, i) => i));
    const first = posts[0].body.camera_context;
    for (let i = 0; i < count; i++) {
      const { body, at } = posts[i],
        camera = body.camera_context;
      expect(camera.source_id).toBe(first.source_id);
      expect(camera.epoch).toBe(first.epoch);
      expect(camera.layout).toBe(layout);
      expect(body.frames).toHaveLength(4);
      expect(
        new Set(body.frames.map((frame: any) => frame.at_seconds)).size,
      ).toBe(4);
      if (i) expect(at - posts[i - 1].at).toBeGreaterThanOrEqual(1900);
      const sample = await pixels(page, body.frames[0].jpeg_base64);
      expect(sample.width).toBeLessThan(320);
      expect(sample.height).toBeLessThan(220);
      checkColour(sample.rgb, i);
      const row = page
        .locator(".interaction-result")
        .filter({ hasText: `Camera ${i + 1} · ${layout}` });
      await expect(row).toHaveCount(1);
      await row.locator("summary").click();
      const saved = await row
        .locator("img")
        .first()
        .evaluate(async (img: HTMLImageElement) => {
          await img.decode();
          const canvas = document.createElement("canvas");
          canvas.width = img.naturalWidth;
          canvas.height = img.naturalHeight;
          canvas.getContext("2d")!.drawImage(img, 0, 0);
          return canvas.toDataURL("image/jpeg").split(",")[1];
        });
      checkColour((await pixels(page, saved)).rgb, i);
    }
    await expect.poll(() => page.locator(".ld-track").count()).toBe(count);
    for (let i = 1; i <= count; i++)
      await expect(
        page
          .locator(".ld-track > span")
          .filter({ hasText: new RegExp(`^Camera ${i} · Person #[1-9]\\d*$`) }),
      ).toHaveCount(1);
    const pose = await page.evaluate(() => (window as any).__all);
    expect(Object.values(pose.firstTracks)).toEqual(
      Array.from({ length: count }, (_, i) => `Camera ${i + 1} · Person #1`),
    );
    expect(pose.modes).toEqual(["IMAGE"]);
    expect(pose.workers).toBe(1);
    expect(pose.sound).toBe(0);
    for (let i = 0; i < count; i++)
      expect(
        pose.pose.some((p: any) => {
          try {
            checkColour(p.rgb, i);
            return true;
          } catch {
            return false;
          }
        }),
      ).toBe(true);
    if (layout === "2x2") {
      const events = await (
        await page.request.get(`${installation.url}/api/live-events`)
      ).json();
      expect(events).toHaveLength(4);
      for (let i = 0; i < 4; i++) {
        const event = events.find(
          (row: any) => row.camera_context?.camera_index === i,
        );
        expect(event).toMatchObject({
          track_id: 1,
          sound_requested: false,
          camera_context: posts[i].body.camera_context,
        });
        await expect(
          page
            .locator(".ld-event")
            .filter({ hasText: `Camera ${i + 1} · 2x2` }),
        ).toHaveCount(1);
      }
      const result = page
        .locator(".interaction-result")
        .filter({ hasText: "Camera 3 · 2x2" });
      await result.getByRole("button", { name: "Useful", exact: true }).click();
      await result
        .getByRole("button", {
          name: "Create case from reviewed observation",
          exact: true,
        })
        .click();
      const form = page.getByRole("form", {
        name: "Create case from observation",
        exact: true,
      });
      await form
        .getByLabel("Case title", { exact: true })
        .fill("Synthetic Camera 3 follow-up");
      await form
        .getByLabel("Staff reviewed notes", { exact: true })
        .fill("Synthetic camera sample checked; no theft conclusion.");
      const receipt = page.waitForResponse(
        (r) => r.url().endsWith("/case") && r.request().method() === "POST",
      );
      await form
        .getByRole("button", { name: "Create reviewed case", exact: true })
        .click();
      expect((await receipt).status()).toBe(201);
    }
    expect(errors).toEqual([]);
    await page
      .getByRole("button", { name: "Stop detection", exact: true })
      .click();
    await expect
      .poll(() => page.evaluate(() => (window as any).__all.workers))
      .toBe(0);
  });

test("a Camera 6 observation gap retires its anonymous ID without joining other camera histories", async ({
  page,
  installation,
}) => {
  await openGrid(page, installation, "2x3");
  await begin(page, false);
  const labels = page.locator(".ld-track > span");
  await expect(labels).toHaveText(
    Array.from({ length: 6 }, (_, i) => `Camera ${i + 1} · Person #1`),
  );

  await page.evaluate(() => ((window as any).__all.occludedCamera = 6));
  // Observe a real series of cropped inference requests spanning the gap;
  // do not advance clocks or depend on a fixed sleep completing on CI.
  await expect
    .poll(() =>
      page.evaluate(() => {
        const frames = (window as any).__all.occludedFrames as number[];
        return frames.length > 1 ? frames.at(-1)! - frames[0] : 0;
      }),
    )
    .toBeGreaterThanOrEqual(1500);
  await expect(labels).toHaveText(
    Array.from({ length: 5 }, (_, i) => `Camera ${i + 1} · Person #1`),
  );

  await page.evaluate(() => ((window as any).__all.occludedCamera = null));
  await expect(labels).toHaveText([
    ...Array.from({ length: 5 }, (_, i) => `Camera ${i + 1} · Person #1`),
    "Camera 6 · Person #2",
  ]);
  const state = await page.evaluate(() => (window as any).__all);
  expect(state.modes).toEqual(["IMAGE"]);
  expect(state.workers).toBe(1);
  expect(state.sound).toBe(0);
  await page
    .getByRole("button", { name: "Stop detection", exact: true })
    .click();
  await expect(labels).toHaveCount(0);
  await expect
    .poll(() => page.evaluate(() => (window as any).__all.workers))
    .toBe(0);
});

test("a held model job never queues another camera; layout invalidation cancels it and disarms before late completion", async ({
  page,
  installation,
}) => {
  test.setTimeout(60000);
  await writeFile(join(installation.directory, "delay"), "7");
  const posts: any[] = [];
  let cancellations = 0;
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().endsWith("/api/interactions/jobs"))
      posts.push(r.postDataJSON());
    if (r.url().endsWith("/cancel")) cancellations++;
  });
  await openGrid(page, installation, "3x2");
  await begin(page);
  const armed = page.getByLabel(
    "Experimental attention alarm for all confirmed cameras",
    { exact: true },
  );
  await expect(armed).toBeDisabled();
  await page
    .getByRole("button", { name: "Test all-camera alarm sound", exact: true })
    .click();
  await page.waitForTimeout(2050);
  await page
    .getByRole("button", {
      name: "I heard the all-camera test tone",
      exact: true,
    })
    .click();
  await armed.check();
  await expect.poll(() => posts.length, { timeout: 10000 }).toBe(1);
  const strip = page.getByRole("region", {
    name: "All-camera submitted sequence",
    exact: true,
  });
  await expect(strip.locator("img")).toHaveCount(4);
  const frozen = await strip.locator("figcaption").allTextContents();
  await page.waitForTimeout(2300);
  expect(posts).toHaveLength(1);
  expect(await strip.locator("figcaption").allTextContents()).toEqual(frozen);
  const cards = page.locator(".all-camera-cards");
  await expect(cards).toContainText("4/4 fresh frames");
  await page
    .getByRole("button", { name: "Detect layout again", exact: true })
    .click();
  await expect.poll(() => cancellations).toBeGreaterThan(0);
  await expect
    .poll(() => page.evaluate(() => (window as any).__all.workers))
    .toBe(0);
  await page.waitForTimeout(7800);
  expect(posts).toHaveLength(1);
  expect(await page.evaluate(() => (window as any).__all.sound)).toBe(1);
  await expect(page.locator(".interaction-alert")).toHaveCount(0);
  await page
    .getByRole("button", { name: "Use this layout", exact: true })
    .click();
  await page
    .getByRole("radio", { name: "All 6 confirmed cameras", exact: true })
    .check();
  await expect(
    page.getByLabel("Enable all-camera product analysis", { exact: true }),
  ).not.toBeChecked();
  await expect(armed).not.toBeChecked();
});

test("a failed model job pauses automatic all-camera analysis", async ({
  page,
  installation,
}) => {
  test.setTimeout(25000);
  await writeFile(join(installation.directory, "fail"), "1");
  const posts: any[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      request.url().endsWith("/api/interactions/jobs")
    )
      posts.push(request.postDataJSON());
  });
  await openGrid(page, installation, "2x2");
  await begin(page);
  const automatic = page.getByLabel("Analyse all cameras automatically", {
    exact: true,
  });
  await expect(automatic).not.toBeChecked({ timeout: 12000 });
  const status = page
    .getByRole("region", { name: "All-camera product analysis" })
    .locator(":scope > p[role=status]")
    .last();
  await expect(status).toContainText("Automatic submissions are paused");
  expect(posts).toHaveLength(1);
  await page.waitForTimeout(3000);
  expect(posts).toHaveLength(1);
  await expect(
    page.getByLabel("Experimental attention alarm for all confirmed cameras", {
      exact: true,
    }),
  ).not.toBeChecked();
});

for (const change of [
  "source disconnect",
  "source resize",
  "mode",
  "branch",
  "runtime offline",
] as const)
  test(`${change} stops every camera and prevents a late model alarm`, async ({
    page,
    installation,
  }) => {
    test.setTimeout(30000);
    await writeFile(join(installation.directory, "delay"), "3");
    const posts: any[] = [];
    page.on("request", (r) => {
      if (r.method() === "POST" && r.url().endsWith("/api/interactions/jobs"))
        posts.push(r.postDataJSON());
    });
    await openGrid(page, installation, "2x2");
    await begin(page);
    const soundTest = page.getByRole("button", {
      name: "Test all-camera alarm sound",
      exact: true,
    });
    await expect(soundTest).toBeEnabled();
    // The runtime heartbeat can re-render this region while Playwright is
    // performing its comparatively slow scroll/stability checks on Windows.
    // Dispatch on the current enabled button; mouse actionability is covered
    // by the dedicated commissioning journey above.
    await soundTest.dispatchEvent("click");
    const heard = page.getByRole("button", {
      name: "I heard the all-camera test tone",
      exact: true,
    });
    await expect(heard).toBeEnabled();
    await heard.dispatchEvent("click");
    await page
      .getByLabel("Experimental attention alarm for all confirmed cameras", {
        exact: true,
      })
      .check();
    await expect.poll(() => posts.length, { timeout: 9000 }).toBe(1);
    if (change === "source disconnect")
      await page.evaluate(() => (window as any).__all.disconnect());
    else if (change === "source resize")
      await page.evaluate(() => (window as any).__all.resize());
    else if (change === "mode")
      await page
        .getByRole("radio", { name: "Selected camera only", exact: true })
        .check();
    else if (change === "branch")
      await page
        .getByLabel("Active pharmacy branch")
        .selectOption(installation.south);
    else await page.context().setOffline(true);
    await expect
      .poll(() => page.evaluate(() => (window as any).__all.workers), {
        timeout: 5000,
      })
      .toBe(0);
    await page.waitForTimeout(3800);
    expect(posts).toHaveLength(1);
    expect(await page.evaluate(() => (window as any).__all.sound)).toBe(1);
    await expect(page.locator(".interaction-alert")).toHaveCount(0);
    if (change === "runtime offline") {
      await page.context().setOffline(false);
      await page.waitForTimeout(2500);
      expect(await page.evaluate(() => (window as any).__all.workers)).toBe(0);
      expect(posts).toHaveLength(1);
    }
  });

test("fresh camera-labelled alarms require commissioning and share one cooldown; historical refresh does not replay sound", async ({
  page,
  installation,
}) => {
  test.setTimeout(30000);
  await openGrid(page, installation, "2x2");
  await begin(page);
  const alarm = page.getByLabel(
    "Experimental attention alarm for all confirmed cameras",
    { exact: true },
  );
  await expect(alarm).not.toBeChecked();
  await expect(alarm).toBeDisabled();
  await page
    .getByRole("button", { name: "Test all-camera alarm sound", exact: true })
    .click();
  await page
    .getByRole("button", {
      name: "I heard the all-camera test tone",
      exact: true,
    })
    .click();
  await alarm.check();
  await expect
    .poll(() => page.evaluate(() => (window as any).__all.sound), {
      timeout: 12000,
    })
    .toBe(2);
  await expect(
    page.getByRole("button", {
      name: "Acknowledge Camera 1 attention",
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.locator(".interaction-result")).toHaveCount(2, {
    timeout: 8000,
  });
  await page
    .getByLabel("Analyse all cameras automatically", { exact: true })
    .uncheck();
  await page
    .getByRole("button", { name: "Refresh model & history", exact: true })
    .click();
  expect(await page.evaluate(() => (window as any).__all.sound)).toBe(2);
  await page
    .getByRole("button", {
      name: "Stop sound & disarm all cameras",
      exact: true,
    })
    .click();
  await expect(alarm).not.toBeChecked();
  await page
    .getByRole("radio", { name: "Selected camera only", exact: true })
    .check();
  await page
    .getByRole("radio", { name: "All 4 confirmed cameras", exact: true })
    .check();
  await page
    .getByRole("button", { name: "Start detection", exact: true })
    .click();
  await expect(alarm).not.toBeChecked();
  await expect(
    page.getByLabel("Enable all-camera product analysis", { exact: true }),
  ).not.toBeChecked();
  expect(await page.evaluate(() => (window as any).__all.sound)).toBe(2);
});

test("all-camera playback failure disarms sound and pauses automatic submissions", async ({
  page,
  installation,
}) => {
  test.setTimeout(60000);
  await openGrid(page, installation, "2x2");
  await begin(page);
  const alarm = page.getByLabel(
    "Experimental attention alarm for all confirmed cameras",
    { exact: true },
  );
  await page
    .getByRole("button", { name: "Test all-camera alarm sound", exact: true })
    .click();
  await page
    .getByRole("button", {
      name: "I heard the all-camera test tone",
      exact: true,
    })
    .click();
  await page.evaluate(() => ((window as any).__all.failSound = true));
  await alarm.check();
  await expect
    .poll(() => page.evaluate(() => (window as any).__all.soundAttempts), {
      timeout: 30000,
    })
    .toBe(2);
  await expect(
    page.getByLabel("Analyse all cameras automatically", { exact: true }),
  ).not.toBeChecked();
  await expect(alarm).not.toBeChecked();
  await expect(
    page.getByText(/sound failed and automatic submissions are paused/i),
  ).toBeVisible();
  expect(await page.evaluate(() => (window as any).__all.sound)).toBe(1);
});

test("slow per-camera pose processing exposes lost continuity and a hung worker stops all processing", async ({
  page,
  installation,
}) => {
  test.setTimeout(25000);
  await openGrid(page, installation, "3x2");
  await page.evaluate(() => ((window as any).__all.delay = 450));
  await begin(page, false);
  await expect(
    page.getByRole("region", {
      name: "Per-camera body tracking coverage",
      exact: true,
    }),
  ).toContainText("Degraded: movement history reset", { timeout: 9000 });
  expect(await page.evaluate(() => (window as any).__all.workers)).toBe(1);
  await page.evaluate(() => ((window as any).__all.delay = 2000));
  await expect
    .poll(() => page.evaluate(() => (window as any).__all.workers), {
      timeout: 4000,
    })
    .toBe(0);
  await expect(
    page.getByText("DETECTION STOPPED", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByLabel("Enable product interaction analysis", { exact: true }),
  ).not.toBeChecked();
  await expect(
    page.getByLabel("Experimental product attention alarm", { exact: true }),
  ).not.toBeChecked();
  expect(await page.evaluate(() => (window as any).__all.sound)).toBe(0);
});

test("all-camera controls and per-camera coverage fit a phone viewport and remain keyboard accessible", async ({
  page,
  installation,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openGrid(page, installation, "3x2");
  await begin(page, false);
  await expect(page.locator(".all-camera-cards article")).toHaveCount(6);
  await page
    .getByRole("button", { name: "Stop all-camera analysis", exact: true })
    .click();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBe(true);
  const result = await new AxeBuilder({ page })
    .include(".all-camera-analysis")
    .include(".ld-camera-mode")
    .analyze();
  expect(result.violations).toEqual([]);
  await page.screenshot({
    path: ".local/all-camera-mobile.png",
    fullPage: true,
  });
});
