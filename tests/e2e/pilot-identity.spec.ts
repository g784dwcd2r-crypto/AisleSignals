import { test as base, expect, Page } from "@playwright/test";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";

// Each test owns an ephemeral loopback socket and pilot database. No actual
// pharmacy records, preview server, configured model or user credentials are used.
const fixtureServer = String.raw`
import json, os, socket, sys
from pathlib import Path
for key in list(os.environ):
    if key.startswith('AISLESIGNALS_'):
        del os.environ[key]
listener = socket.socket()
listener.bind(('127.0.0.1', 0))
listener.listen(128)
port = listener.getsockname()[1]
unavailable_model = socket.socket()
unavailable_model.bind(('127.0.0.1', 0))
os.environ['AISLESIGNALS_MODE'] = 'pilot'
os.environ['AISLESIGNALS_PORT'] = str(port)
# macOS exposes /var through /private/var. The test owns this temporary
# directory; use its canonical path without relaxing the pilot symlink guard.
os.environ['AISLESIGNALS_DB_PATH'] = str(Path(sys.argv[1]).resolve() / 'pilot.db')
os.environ['AISLESIGNALS_WEB_DIST'] = str(Path.cwd() / 'apps/web/dist')
os.environ['AISLESIGNALS_VISION_URL'] = 'http://127.0.0.1:' + str(unavailable_model.getsockname()[1])
from services.api.store import Store
from services.api.pilot_identity import initialise, add_site, grant_user
store = Store(os.environ['AISLESIGNALS_DB_PATH'], mode='pilot')
branches = {}
if sys.argv[2] == 'provision':
    initial = initialise(store, 'Synthetic Acceptance Group', 'Synthetic North Branch', 'manager@example.test', 'Named Test Manager', 'Synthetic acceptance passphrase 73!')
    south = add_site(store, initial['site']['organisation_id'], 'Synthetic South Branch')
    add_site(store, initial['site']['organisation_id'], 'Unassigned Test Branch')
    grant_user(store, 'manager@example.test', south['id'], 'REVIEWER')
    branches = {'north': initial['site']['id'], 'south': south['id']}
from services.api.app import app
import uvicorn
print('PILOT_READY ' + json.dumps({'url': 'http://127.0.0.1:' + str(port), **branches}), flush=True)
uvicorn.Server(uvicorn.Config(app, log_level='warning', access_log=False, proxy_headers=False)).run(sockets=[listener])
`;

type Pilot = { url: string; north: string; south: string };
const test = base.extend<{ pilot: Pilot; emptyPilot: boolean }>({
  emptyPilot: [false, { option: true }],
  pilot: async ({ emptyPilot }, use) => {
    const directory = await mkdtemp(
      join(tmpdir(), "aislesignals-pilot-browser-"),
    );
    const child = spawn(
      process.env.AISLESIGNALS_TEST_PYTHON || "python",
      [
        "-u",
        "-c",
        fixtureServer,
        directory,
        emptyPilot ? "empty" : "provision",
      ],
      { cwd: process.cwd(), stdio: ["ignore", "pipe", "pipe"] },
    );
    let output = "",
      errors = "";
    const closed = new Promise<void>((resolveClosed) =>
      child.once("close", () => resolveClosed()),
    );
    try {
      const ready = new Promise<Pilot>((resolveReady, reject) => {
        child.stdout.on("data", (chunk) => {
          output += String(chunk);
          const line = output
            .split("\n")
            .find((value) => value.startsWith("PILOT_READY "));
          if (line) resolveReady(JSON.parse(line.slice("PILOT_READY ".length)));
        });
        child.stderr.on("data", (chunk) => {
          errors += String(chunk);
        });
        child.once("error", reject);
        child.once("exit", (code) =>
          reject(new Error(`Isolated pilot API exited (${code}): ${errors}`)),
        );
      });
      let timeout: NodeJS.Timeout | undefined;
      const server = await Promise.race([
        ready,
        new Promise<never>((_, reject) => {
          timeout = setTimeout(
            () => reject(new Error("Isolated pilot API did not become ready")),
            20_000,
          );
        }),
      ]).finally(() => clearTimeout(timeout));
      await expect
        .poll(async () => {
          try {
            return (await fetch(`${server.url}/api/health`)).status;
          } catch {
            return 0;
          }
        })
        .toBe(200);
      await use(server);
    } finally {
      if (child.exitCode === null) child.kill("SIGTERM");
      await closed;
      await rm(directory, { recursive: true, force: true });
    }
  },
});

async function login(page: Page, pilot: Pilot) {
  await page.goto(pilot.url);
  await page
    .getByLabel("Email address", { exact: true })
    .fill("manager@example.test");
  await page
    .getByLabel("Password", { exact: true })
    .fill("Synthetic acceptance passphrase 73!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByLabel("Active pharmacy branch")).toHaveValue(
    pilot.north,
  );
  await expect(
    page.getByRole("button", { name: "Open LIVE DETECTION", exact: true }),
  ).toBeVisible();
}

async function createCase(page: Page, title: string) {
  await page
    .getByRole("button", { name: "New manual case", exact: true })
    .first()
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Case title", { exact: true }).fill(title);
  await dialog
    .getByLabel("Initial notes", { exact: true })
    .fill("Synthetic acceptance fixture. No actual pharmacy incident.");
  await dialog
    .getByRole("button", { name: "Create manual case", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: title, exact: true }),
  ).toBeVisible();
}

test.describe("unconfigured installation", () => {
  test.use({ emptyPilot: true });
  test("requires local named-account setup without exposing demo shortcuts", async ({
    page,
    pilot,
  }) => {
    await page.goto(pilot.url);
    await expect(
      page.getByRole("heading", {
        name: "Create the first owner account",
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Sign in", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByLabel("Owner email address", { exact: true }),
    ).toHaveValue("");
    await expect(
      page.getByLabel("Owner passphrase", { exact: true }),
    ).toHaveValue("");
    await expect(
      page.getByLabel("One-time setup code", { exact: true }),
    ).toHaveValue("");
    await expect(
      page.getByText("Public demo credentials", { exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("combobox", { name: "Demo account", exact: true }),
    ).toHaveCount(0);
    const denied = await page.request.post(`${pilot.url}/api/login`, {
      headers: { Origin: pilot.url },
      data: { email: "manager@harbour.demo", password: "AisleDemo!2026" },
    });
    expect(denied.status()).toBe(401);
    const accessibility = await new AxeBuilder({ page }).analyze();
    expect(accessibility.violations).toEqual([]);
  });
});

test("named account switches only allowed branches and clears unsaved case data", async ({
  page,
  pilot,
}) => {
  await login(page, pilot);
  const picker = page.getByLabel("Active pharmacy branch");
  await expect(picker.locator("option")).toHaveText([
    "Synthetic North Branch · Manager",
    "Synthetic South Branch · Reviewer",
  ]);
  await expect(
    page.getByRole("button", { name: "Run a scenario", exact: true }),
  ).toHaveCount(0);
  await createCase(page, "Synthetic branch-isolated case");
  await page.getByLabel("Reviewed notes").fill("Unsaved north-only test note.");
  const old = await (await page.request.get(`${pilot.url}/api/session`)).json();
  // Hold one old-branch heartbeat across the server-side session rotation.
  // The branch transition must abort it before a late 401 can end the new
  // session. This was observable on slower Windows runners.
  let releaseHeartbeat!: () => void;
  let heartbeatReached!: () => void;
  const heartbeatGate = new Promise<void>((done) => {
    releaseHeartbeat = done;
  });
  const heartbeatStarted = new Promise<void>((done) => {
    heartbeatReached = done;
  });
  await page.route("**/api/runtime/health", async (route) => {
    heartbeatReached();
    await heartbeatGate;
    try {
      await route.continue();
    } catch {
      // Expected when branch switching aborts the old heartbeat.
    }
  });
  await heartbeatStarted;
  const switched = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/session/site") &&
      response.request().method() === "POST",
  );
  await picker.selectOption(pilot.south);
  expect((await switched).status()).toBe(200);
  releaseHeartbeat();
  await expect(picker).toHaveValue(pilot.south);
  await expect(
    page.getByRole("button", { name: "Open LIVE DETECTION", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", {
      name: "Synthetic branch-isolated case",
      exact: true,
    }),
  ).toHaveCount(0);
  const next = await (
    await page.request.get(`${pilot.url}/api/session`)
  ).json();
  expect(next.csrf_token).not.toBe(old.csrf_token);
  expect(next.user.role).toBe("REVIEWER");
  const south = await (
    await page.request.get(`${pilot.url}/api/bootstrap`)
  ).json();
  expect(south.incidents).toEqual([]);
  await createCase(page, "Synthetic south reviewer case");
  await expect(
    page.getByLabel("Recorded loss (€)", { exact: true }),
  ).toHaveCount(0);
  await picker.selectOption(pilot.north);
  await expect(
    page.getByRole("button", { name: "Open LIVE DETECTION", exact: true }),
  ).toBeVisible();
  const north = await (
    await page.request.get(`${pilot.url}/api/bootstrap`)
  ).json();
  expect(north.incidents.map((item: { title: string }) => item.title)).toEqual([
    "Synthetic branch-isolated case",
  ]);
  expect(north.incidents[0].notes).not.toContain("Unsaved");
});

test("branch switch releases selected video before the server responds", async ({
  page,
  pilot,
}) => {
  await page.addInitScript(() => {
    const active = new Set<string>();
    (window as any).__pilotVideoUrls = active;
    const create = URL.createObjectURL.bind(URL),
      revoke = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = (source) => {
      const url = create(source);
      active.add(url);
      return url;
    };
    URL.revokeObjectURL = (url) => {
      active.delete(url);
      revoke(url);
    };
  });
  await login(page, pilot);
  await page
    .getByRole("button", { name: "LIVE DETECTION", exact: true })
    .click();
  await page
    .getByLabel("Choose CCTV recording")
    .setInputFiles(resolve("tests/fixtures/synthetic-video.webm"));
  await expect(page.getByText(/^Recorded CCTV ready\./)).toBeVisible();
  expect(await page.evaluate(() => (window as any).__pilotVideoUrls.size)).toBe(
    1,
  );
  let finish!: () => void;
  const hold = new Promise<void>((resolveHold) => {
    finish = resolveHold;
  });
  await page.route("**/api/session/site", async (route) => {
    await hold;
    await route.continue();
  });
  try {
    await page.getByLabel("Active pharmacy branch").selectOption(pilot.south);
    await expect(
      page.getByText("Switching pharmacy branch", { exact: true }),
    ).toBeVisible();
    await expect(page.locator(".live-detection")).toHaveCount(0);
    expect(
      await page.evaluate(() => (window as any).__pilotVideoUrls.size),
    ).toBe(0);
  } finally {
    finish();
  }
  await expect(
    page.getByRole("button", { name: "Open LIVE DETECTION", exact: true }),
  ).toBeVisible();
});

test("another tab switching branches locks stale views and cannot move a case into the new branch", async ({
  page,
  context,
  pilot,
}) => {
  await login(page, pilot);
  await createCase(page, "Synthetic stale-tab case");
  const stale = await context.newPage();
  await stale.goto(pilot.url);
  await expect(stale.getByLabel("Active pharmacy branch")).toHaveValue(
    pilot.north,
  );
  await stale.getByRole("button", { name: "Casebook", exact: true }).click();
  await stale
    .getByRole("button")
    .filter({ hasText: "Synthetic stale-tab case" })
    .first()
    .click();
  await stale
    .getByLabel("Reviewed notes")
    .fill("This stale edit must not reach another branch.");
  const old = await (await page.request.get(`${pilot.url}/api/session`)).json();
  await page.getByLabel("Active pharmacy branch").selectOption(pilot.south);
  await expect(
    page.getByRole("button", { name: "Open LIVE DETECTION", exact: true }),
  ).toBeVisible();
  await expect(
    stale.getByRole("button", { name: "Sign in", exact: true }),
  ).toBeVisible();
  await expect(stale.getByLabel("Reviewed notes")).toHaveCount(0);
  const rejected = await stale.request.post(`${pilot.url}/api/incidents`, {
    headers: {
      Origin: pilot.url,
      "X-CSRF-Token": old.csrf_token,
      "X-AisleSignals-Site": pilot.north,
    },
    data: { title: "Stale branch write", notes: "Synthetic rejected write." },
  });
  expect(rejected.status()).toBe(403);
  expect(
    (await (await page.request.get(`${pilot.url}/api/bootstrap`)).json())
      .incidents,
  ).toEqual([]);
});

test("uncertain branch switch clears the view and requires explicit sign-in", async ({
  page,
  pilot,
}) => {
  await login(page, pilot);
  await page.route("**/api/session/site", (route) =>
    route.abort("connectionrefused"),
  );
  await page.getByLabel("Active pharmacy branch").selectOption(pilot.south);
  await expect(
    page.getByText(/The branch switch could not be confirmed\./),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Sign in", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("Active pharmacy branch")).toHaveCount(0);
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Sign in", exact: true }),
  ).toBeVisible();
});
