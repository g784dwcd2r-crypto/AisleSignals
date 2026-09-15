import { test as base, expect, type Page } from "@playwright/test";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
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
        time.sleep(2)
        return super().analyze(frames)
app.state.interactions.provider=DelayedReviewProvider()
import uvicorn
print('CASE_READY '+json.dumps({'url':'http://127.0.0.1:'+str(port),'north':initial['site']['id'],'south':other['id']}),flush=True)
uvicorn.Server(uvicorn.Config(app,log_level='warning',access_log=False,proxy_headers=False)).run(sockets=[listener])
`;
type Installation = { url: string; north: string; south: string };
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
      await use(installation);
    } finally {
      if (child.exitCode === null) child.kill("SIGTERM");
      await closed;
      await rm(directory, { recursive: true, force: true });
    }
  },
});

async function open(page: Page, installation: Installation) {
  await page.addInitScript(() => {
    class EmptyPoseWorker {
      onmessage: ((event: any) => void) | null = null;
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
    (window as any).Worker = EmptyPoseWorker;
  });
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
  await page
    .getByRole("button", { name: "LIVE DETECTION", exact: true })
    .click();
  await page
    .getByLabel("Choose CCTV recording")
    // Keep the real decoder running while the API completes and staff review.
    // The short scanner fixture can end and stop polling before a result appears.
    .setInputFiles(resolve("tests/fixtures/synthetic-case-video.webm"));
  await page
    .getByRole("combobox", { name: "Camera layout", exact: true })
    .selectOption("single");
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
  await expect(page.locator(".interaction-result")).toHaveCount(1);
}

async function action(
  page: Page,
  installation: Installation,
  path: string,
  body: unknown,
  method = "POST",
) {
  const session = (
    await page.request.get(`${installation.url}/api/session`)
  ).json();
  const context = await session;
  return page.request.fetch(`${installation.url}/api${path}`, {
    method,
    data: body,
    headers: {
      Origin: installation.url,
      "X-CSRF-Token": context.csrf_token,
      "X-AisleSignals-Site": context.current_site_id,
      "Idempotency-Key": crypto.randomUUID(),
    },
  });
}

test("staff review creates one unassessed linked case, opens its real evidence and handles deletion", async ({
  page,
  installation,
}) => {
  await open(page, installation);
  const result = page.locator(".interaction-result");
  await expect(
    result.getByRole("button", {
      name: "Create case from reviewed observation",
      exact: true,
    }),
  ).toBeDisabled();
  await result.locator("summary").click();
  await expect
    .poll(() =>
      result
        .locator("img")
        .evaluateAll((images) =>
          images.every((image) => (image as HTMLImageElement).naturalWidth > 0),
        ),
    )
    .toBe(true);
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
    .fill("Synthetic source follow-up");
  await form
    .getByLabel("Staff reviewed notes", { exact: true })
    .fill(
      "Staff will inspect the original recording. No financial loss is established.",
    );
  const receipt = page.waitForResponse(
    (response) =>
      response.url().endsWith("/case") &&
      response.request().method() === "POST",
  );
  await form
    .getByRole("button", { name: "Create reviewed case", exact: true })
    .dblclick();
  const response = await receipt;
  expect(response.status()).toBe(201);
  const linked = await response.json();
  expect(
    (await page.request.get(`${installation.url}/api/bootstrap`)).ok(),
  ).toBe(true);
  expect(
    (await (await page.request.get(`${installation.url}/api/bootstrap`)).json())
      .incidents,
  ).toHaveLength(1);
  let releaseActionRefresh!: () => void;
  const heldActionRefresh = new Promise<void>((resolve) => {
    releaseActionRefresh = resolve;
  });
  let actionRefreshHeld = false;
  await page.route("**/api/bootstrap", async (route) => {
    if (!actionRefreshHeld) {
      actionRefreshHeld = true;
      await heldActionRefresh;
    }
    await route.continue();
  });
  await result
    .getByRole("button", { name: "Open linked case", exact: true })
    .click();
  await expect.poll(() => actionRefreshHeld).toBe(true);
  await page
    .getByRole("button", { name: "Refresh workspace", exact: true })
    .click();
  releaseActionRefresh();
  await expect(page.getByLabel("Case title", { exact: true })).toHaveValue(
    "Synthetic source follow-up",
  );
  await page.unrouteAll({ behavior: "wait" });
  // A refresh that races navigation may leave Chromium's first image requests
  // pending even after the case metadata wins. Exercise the visible recovery
  // action after removing the synthetic route hold so every frame gets a fresh,
  // incident-scoped request.
  await page
    .getByRole("button", { name: "Refresh linked evidence", exact: true })
    .click();
  const source = page.getByRole("region", {
    name: "Linked product observation",
    exact: true,
  });
  await expect(source).toContainText("Unverified model context");
  await expect(source).toContainText("Recorded-video test");
  await expect(source.getByRole("img")).toHaveCount(4);
  await expect
    .poll(
      () =>
        source
          .getByRole("img")
          .evaluateAll(
            (images) =>
              images.length === 4 &&
              images.every(
                (image) =>
                  (image as HTMLImageElement).complete &&
                  (image as HTMLImageElement).naturalWidth > 0,
              ),
          ),
      { timeout: 15000 },
    )
    .toBe(true);
  expect(linked.incident.classification).toBe("UNASSESSED");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() =>
      page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    )
    .toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect(
    (
      await action(
        page,
        installation,
        `/interactions/${linked.interaction.id}`,
        undefined,
        "DELETE",
      )
    ).status(),
  ).toBe(200);
  await source
    .getByRole("button", { name: "Refresh linked evidence", exact: true })
    .click();
  await expect(source).toContainText("Sampled JPEGs deleted");
  await expect(source.getByRole("img")).toHaveCount(0);
  await source
    .getByText("Source identifiers and evidence hashes", { exact: true })
    .click();
  await expect(source).toContainText(
    linked.incident.interaction_source.frames[0].sha256,
  );
});

test("a changed staff review conflicts instead of creating a stale case; branch switch clears the draft", async ({
  page,
  installation,
}) => {
  await open(page, installation);
  const result = page.locator(".interaction-result");
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
    .getByLabel("Staff reviewed notes", { exact: true })
    .fill("Synthetic stale draft should not become a case.");
  const item = (
    await (
      await page.request.get(`${installation.url}/api/interactions`)
    ).json()
  ).items[0];
  expect(
    (
      await action(page, installation, `/interactions/${item.id}/review`, {
        expected_version: item.version,
        outcome: "UNCLEAR",
        note: "Synthetic independent reviewer changed the outcome.",
      })
    ).status(),
  ).toBe(200);
  await form
    .getByRole("button", { name: "Create reviewed case", exact: true })
    .click();
  await expect(
    page.getByText("This observation changed or already has a case.", {
      exact: false,
    }),
  ).toBeVisible();
  await expect(
    result.getByRole("button", { name: "Unclear", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  expect(
    (await (await page.request.get(`${installation.url}/api/bootstrap`)).json())
      .incidents,
  ).toHaveLength(0);
  await result
    .getByRole("button", {
      name: "Create case from reviewed observation",
      exact: true,
    })
    .click();
  await form
    .getByLabel("Staff reviewed notes", { exact: true })
    .fill("Synthetic branch-local unsaved note.");
  await page
    .getByLabel("Active pharmacy branch")
    .selectOption(installation.south);
  await expect(form).toHaveCount(0);
  await page.getByRole("button", { name: "Casebook", exact: true }).click();
  await expect(
    page.getByText("No cases in this view", { exact: true }),
  ).toBeVisible();
  expect(
    (
      await page.request.get(
        `${installation.url}/api/interactions/${item.id}/frames/0`,
      )
    ).status(),
  ).toBe(404);
});
