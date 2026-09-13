import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

type Layout = "2x2" | "3x2" | "2x3" | "blank";
async function fixture(
  page: Page,
  layout: Layout,
  delayed = false,
  initiallyBlank = false,
) {
  let submitted: any = null,
    cancelled = 0;
  const id = "aaaaaaaa-2222-4222-8222-222222222222";
  await page.addInitScript(
    ({ layout, initiallyBlank }) => {
      const state = ((window as any).__grid = {
        layout,
        initiallyBlank,
        holdDecoded: initiallyBlank,
        audio: 0,
        draws: 0,
      });
      // Hold decoded-pixel readiness at the media boundary so the early manual
      // selection regression cannot accidentally pass after the first scan.
      const readiness = Object.getOwnPropertyDescriptor(
        HTMLMediaElement.prototype,
        "readyState",
      )!.get!;
      Object.defineProperty(HTMLMediaElement.prototype, "readyState", {
        configurable: true,
        get() {
          return state.holdDecoded ? 1 : readiness.call(this);
        },
      });
      class EmptyWorker {
        onmessage: ((event: any) => void) | null = null;
        onerror = null;
        postMessage(request: any) {
          request.bitmap?.close();
          queueMicrotask(() =>
            this.onmessage?.({
              data:
                request.type === "init"
                  ? { type: "ready" }
                  : { type: "result", id: request.id, poses: [] },
            }),
          );
        }
        terminate() {}
      }
      (window as any).Worker = EmptyWorker;
      const start = OscillatorNode.prototype.start;
      OscillatorNode.prototype.start = function (...args) {
        state.audio++;
        return start.apply(this, args);
      };
      navigator.mediaDevices.getUserMedia = async () => {
        const canvas = document.createElement("canvas");
        canvas.width = 600;
        canvas.height = 400;
        const context = canvas.getContext("2d")!;
        const draw = () => {
          const scene = state.initiallyBlank ? "blank" : state.layout;
          const data = context.createImageData(600, 400);
          const columns = scene === "3x2" ? 3 : 2,
            rows = scene === "2x3" ? 3 : 2;
          const xs = columns === 3 ? [0, 207, 394, 600] : [0, 306, 600];
          const ys = rows === 3 ? [0, 137, 265, 400] : [0, 204, 400];
          const colours = [
            [65, 0, 0],
            [0, 65, 0],
            [0, 0, 65],
            [65, 65, 0],
            [65, 0, 65],
            [0, 65, 65],
          ];
          for (let y = 0; y < 400; y++)
            for (let x = 0; x < 600; x++) {
              const at = (y * 600 + x) * 4;
              let column = 0,
                row = 0;
              while (column < columns - 1 && x >= xs[column + 1]) column++;
              while (row < rows - 1 && y >= ys[row + 1]) row++;
              const v =
                55 +
                ((x * 7 +
                  y * 11 +
                  Math.floor(x / 17) * 23 +
                  Math.floor(y / 13) * 19) %
                  120);
              const colour = colours[row * columns + column];
              const gutter =
                xs.slice(1, -1).some((line) => Math.abs(x - line) <= 2) ||
                ys.slice(1, -1).some((line) => Math.abs(y - line) <= 2);
              const rgb =
                scene === "blank"
                  ? [80, 80, 80]
                  : gutter
                    ? [12, 12, 12]
                    : colour.map((channel) => Math.min(245, v + channel));
              data.data.set([...rgb, 255], at);
            }
          context.putImageData(data, 0, 0);
          state.draws++;
        };
        draw();
        const stream = canvas.captureStream(10);
        const timer = setInterval(() => {
          if (stream.getVideoTracks()[0].readyState === "ended")
            clearInterval(timer);
          else draw();
        }, 100);
        return stream;
      };
    },
    { layout, initiallyBlank },
  );
  await page.route("**/api/interactions/status", (route) =>
    route.fulfill({
      json: {
        ready: true,
        mode: "experimental",
        model: "fixture-only",
        message: "Synthetic workflow provider; no accuracy claim.",
      },
    }),
  );
  await page.route("**/api/interactions", (route) =>
    route.fulfill({ json: { items: [] } }),
  );
  await page.route("**/api/interactions/jobs", async (route) => {
    submitted = route.request().postDataJSON();
    await route.fulfill({ json: { id, status: "pending" } });
  });
  await page.route(`**/api/interactions/jobs/${id}/cancel`, async (route) => {
    cancelled++;
    await route.fulfill({ json: { id, status: "cancelled" } });
  });
  await page.route(`**/api/interactions/jobs/${id}`, async (route) => {
    const input = submitted;
    if (delayed) await new Promise((resolve) => setTimeout(resolve, 1800));
    await route
      .fulfill({
        json: {
          id,
          status: "completed",
          result: {
            id,
            version: 1,
            run_id: input.run_id,
            source_kind: input.source_kind,
            source_label: input.source_label,
            created_at: new Date().toISOString(),
            expires_at: new Date(Date.now() + 86400000).toISOString(),
            model: "fixture-only",
            action: delayed ? "POSSIBLE_CONCEALMENT" : "NORMAL_SHOPPING",
            visibility: "clear",
            person_visible: true,
            product_visible: true,
            sequence_observed: true,
            reason: "Synthetic provider fixture",
            evidence_frame_indices: [0, 3],
            alarm_eligible: delayed,
            inference_ms: 1000,
            review: null,
            frames: input.frames.map((frame: any, index: number) => ({
              at_seconds: frame.at_seconds,
              url: `/api/interactions/${id}/frames/${index}`,
            })),
          },
        },
      })
      .catch(() => {});
  });
  await page.goto("/");
  await page.getByLabel(/^Email/).fill("manager@harbour.demo");
  await page.getByLabel("Password", { exact: true }).fill("AisleDemo!2026");
  await page.getByRole("button", { name: "Open demo workspace" }).click();
  await page
    .getByRole("button", { name: "LIVE DETECTION", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Connect camera", exact: true })
    .click();
  const startDetection = page.getByRole("button", {
    name: "Start detection",
    exact: true,
  });
  if (initiallyBlank) await expect(startDetection).toBeDisabled();
  else await expect(startDetection).toBeEnabled();
  return { submitted: () => submitted, cancelled: () => cancelled };
}
async function confirm(page: Page) {
  await page
    .getByRole("button", { name: "Use this layout", exact: true })
    .click();
}
async function analyse(page: Page) {
  await page
    .getByLabel("Enable product interaction analysis", { exact: true })
    .check();
  await page
    .getByRole("button", { name: "Start detection", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Analyse recent sequence", exact: true }),
  ).toBeEnabled({ timeout: 12000 });
  await page
    .getByRole("button", { name: "Analyse recent sequence", exact: true })
    .click();
}

for (const layout of ["2x2", "3x2", "2x3"] as const)
  test(`proposes ${layout}, confirms measured boundaries and samples the selected camera pixels`, async ({
    page,
  }) => {
    const probe = await fixture(page, layout);
    const picker = page.locator(".camera-layout-picker");
    await expect(
      picker.getByRole("combobox", { name: "Camera layout", exact: true }),
    ).toHaveValue(layout);
    await expect(picker).toContainText("visible separators");
    const overlay = picker.getByRole("button", {
      name: "Select Camera 3",
      exact: true,
    });
    await expect(overlay).toHaveCSS("background-color", "rgba(0, 0, 0, 0.04)");
    await expect(overlay).toHaveCSS("border-top-style", "dashed");
    await expect(
      page.getByRole("button", { name: "Select Camera 3", exact: true }),
    ).toBeDisabled();
    await page
      .getByLabel("Enable product interaction analysis", { exact: true })
      .check();
    await page.getByLabel("Analyse automatically", { exact: true }).check();
    await page
      .getByRole("button", { name: "Start detection", exact: true })
      .click();
    await expect(
      page.getByText("POSE TRACKING RUNNING", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("0/4 fresh sampled frames", { exact: true }),
    ).toBeVisible();
    expect(probe.submitted()).toBeNull();
    await page.getByLabel("Analyse automatically", { exact: true }).uncheck();
    await confirm(page);
    const tile = page.getByRole("button", {
      name: "Select Camera 3",
      exact: true,
    });
    await tile.click();
    await expect(tile).toHaveCSS("background-color", "rgba(68, 195, 124, 0.07)");
    await expect(tile).toHaveCSS("border-top-style", "solid");
    const crop = await tile.evaluate((element) => ({
      x: parseFloat((element as HTMLElement).style.left) / 100,
      y: parseFloat((element as HTMLElement).style.top) / 100,
      width: parseFloat((element as HTMLElement).style.width) / 100,
      height: parseFloat((element as HTMLElement).style.height) / 100,
    }));
    if (layout === "3x2") expect(crop.x).toBeCloseTo(394 / 600, 2);
    else expect(crop.y).toBeCloseTo((layout === "2x3" ? 137 : 204) / 400, 2);
    await expect(
      page.getByRole("button", {
        name: "Analyse recent sequence",
        exact: true,
      }),
    ).toBeEnabled({ timeout: 12000 });
    await page
      .getByRole("button", { name: "Analyse recent sequence", exact: true })
      .click();
    await expect.poll(() => probe.submitted()).not.toBeNull();
    expect(probe.submitted().source_label).toContain(
      `Camera 3 · ${layout} grid`,
    );
    const pixels = await page.evaluate(async (base64) => {
      const image = new Image();
      image.src = `data:image/jpeg;base64,${base64}`;
      await image.decode();
      const canvas = document.createElement("canvas");
      canvas.width = image.width;
      canvas.height = image.height;
      const context = canvas.getContext("2d")!;
      context.drawImage(image, 0, 0);
      const data = context.getImageData(
        Math.floor(image.width / 2) - 8,
        Math.floor(image.height / 2) - 8,
        16,
        16,
      ).data;
      const sums = [0, 0, 0];
      for (let i = 0; i < data.length; i += 4)
        for (let c = 0; c < 3; c++) sums[c] += data[i + c];
      return {
        width: image.width,
        height: image.height,
        rgb: sums.map((sum) => sum / 256),
      };
    }, probe.submitted().frames[0].jpeg_base64);
    // Browsers round percentage text in CSSStyleDeclaration (e.g. 51.0833%).
    // Allow its at-most-one source-pixel rounding error, while independently
    // proving the measured uneven boundary and the selected camera's pixels.
    expect(
      Math.abs(pixels.width - Math.round(600 * crop.width)),
    ).toBeLessThanOrEqual(1);
    expect(
      Math.abs(pixels.height - Math.round(400 * crop.height)),
    ).toBeLessThanOrEqual(1);
    expect(pixels.rgb[2]).toBeGreaterThan(pixels.rgb[0] + 40);
    expect(pixels.rgb[2]).toBeGreaterThan(pixels.rgb[1] + 40);
    await page
      .getByRole("button", { name: "Stop detection", exact: true })
      .click();
  });

for (const change of ["tile", "board", "source"] as const)
  test(`${change} changes cancel old camera jobs and disarm possible-concealment audio`, async ({
    page,
  }) => {
    const probe = await fixture(page, "3x2", true);
    await expect(
      page.getByRole("combobox", { name: "Camera layout", exact: true }),
    ).toHaveValue("3x2");
    await confirm(page);
    await page
      .getByLabel("Enable product interaction analysis", { exact: true })
      .check();
    await page
      .getByRole("button", { name: "Start detection", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Test product alarm sound", exact: true })
      .click();
    await page
      .getByRole("button", { name: "I heard the test tone", exact: true })
      .click();
    await page
      .getByLabel("Experimental product attention alarm", { exact: true })
      .check();
    await expect(
      page.getByRole("button", {
        name: "Analyse recent sequence",
        exact: true,
      }),
    ).toBeEnabled({ timeout: 12000 });
    await page
      .getByRole("button", { name: "Analyse recent sequence", exact: true })
      .click();
    await expect.poll(() => probe.submitted()).not.toBeNull();
    if (change === "tile")
      await page
        .getByRole("button", { name: "Select Camera 2", exact: true })
        .click();
    else if (change === "board") {
      await page
        .getByText("Exclude recorder or browser borders", { exact: true })
        .click();
      await page.getByLabel("Camera board width %", { exact: true }).fill("90");
    } else {
      await page
        .getByRole("button", { name: "Stop detection", exact: true })
        .click();
      await page
        .getByRole("button", { name: "Connect camera", exact: true })
        .click();
    }
    await expect.poll(() => probe.cancelled()).toBeGreaterThan(0);
    await expect(
      page.getByLabel("Experimental product attention alarm", { exact: true }),
    ).not.toBeChecked();
    await expect(
      page.getByRole("button", { name: "Analysing…", exact: true }),
    ).toHaveCount(0);
    await expect(page.locator(".interaction-alert")).toHaveCount(0);
    expect(await page.evaluate(() => (window as any).__grid.audio)).toBe(1);
    if (change !== "tile")
      await expect(
        page.getByText("0/4 fresh sampled frames", { exact: true }),
      ).toBeVisible();
    await page
      .getByRole("button", { name: "Stop detection", exact: true })
      .click();
  });

test("blank source allows an early manual layout, preview becomes usable, and re-detection needs confirmation", async ({
  page,
}) => {
  const probe = await fixture(page, "3x2", false, true);
  const picker = page.locator(".camera-layout-picker");
  await picker
    .getByRole("combobox", { name: "Camera layout", exact: true })
    .selectOption("3x2");
  await expect(
    page.getByRole("button", { name: "Use this layout", exact: true }),
  ).toBeDisabled();
  await page.evaluate(() => {
    (window as any).__grid.holdDecoded = false;
  });
  await expect(
    page.getByRole("button", { name: "Use this layout", exact: true }),
  ).toBeEnabled();
  await confirm(page);
  await expect(
    page.getByRole("button", { name: "Select Camera 1", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.evaluate(() => {
    (window as any).__grid.initiallyBlank = false;
  });
  await page
    .getByRole("button", { name: "Detect layout again", exact: true })
    .click();
  await expect(picker).toContainText("visible separators");
  await expect(
    page.getByRole("button", { name: "Select Camera 1", exact: true }),
  ).toBeDisabled();
  expect(probe.submitted()).toBeNull();
  await confirm(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() =>
      page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    )
    .toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page
    .getByRole("button", { name: "Stop detection", exact: true })
    .click();
});

test("ambiguous frames require explicit Single camera or custom selection", async ({
  page,
}) => {
  const probe = await fixture(page, "blank");
  await page
    .getByLabel("Enable product interaction analysis", { exact: true })
    .check();
  await page
    .getByRole("button", { name: "Start detection", exact: true })
    .click();
  await expect(page.locator(".camera-layout-status")).toContainText(
    "No reliable camera layout",
  );
  await expect(
    page.getByText("0/4 fresh sampled frames", { exact: true }),
  ).toBeVisible();
  expect(probe.submitted()).toBeNull();
  await page
    .getByRole("combobox", { name: "Camera layout", exact: true })
    .selectOption("single");
  await expect(
    page.getByRole("button", { name: "Select Camera 1", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByRole("button", { name: "Analyse recent sequence", exact: true }),
  ).toBeEnabled({ timeout: 12000 });
  await page
    .getByRole("button", { name: "Stop detection", exact: true })
    .click();
});
