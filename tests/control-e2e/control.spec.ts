import {
  test as base,
  expect,
  type Page,
  type APIRequestContext,
} from "@playwright/test";
import { spawn } from "node:child_process";
import { createHmac } from "node:crypto";
import AxeBuilder from "@axe-core/playwright";

type Installation = { url: string; bootstrapToken: string };
const test = base.extend<{ installation: Installation }>({
  installation: async ({}, use) => {
    const child = spawn(
      process.env.CLOUD_TEST_PYTHON || "python",
      [
        "-u",
        "tests/control-e2e/server.py",
        process.env.CONTROL_E2E_PORT || "60644",
      ],
      { cwd: process.cwd(), stdio: ["ignore", "pipe", "pipe"] },
    );
    let output = "",
      errors = "",
      timer: NodeJS.Timeout | undefined;
    const closed = new Promise<void>((resolve) =>
      child.once("close", () => resolve()),
    );
    try {
      const ready = new Promise<Installation>((resolve, reject) => {
        child.stdout.on("data", (chunk) => {
          output += String(chunk);
          const line = output
            .split("\n")
            .find((row) => row.startsWith("CONTROL_READY "));
          if (line) resolve(JSON.parse(line.slice(14)));
        });
        child.stderr.on("data", (chunk) => {
          errors = (errors + String(chunk)).slice(-4000);
        });
        child.once("error", reject);
        child.once("exit", (code) =>
          reject(
            new Error(`Synthetic control fixture exited ${code}: ${errors}`),
          ),
        );
      });
      const installation = await Promise.race([
        ready,
        new Promise<never>((_, reject) => {
          timer = setTimeout(
            () =>
              reject(new Error("Synthetic control fixture startup timed out")),
            20000,
          );
        }),
      ]).finally(() => clearTimeout(timer));
      try {
        await expect
          .poll(
            async () => {
              try {
                return (await fetch(installation.url + "/health/ready")).status;
              } catch {
                return 0;
              }
            },
            { timeout: 10000 },
          )
          .toBe(200);
      } catch (reason) {
        throw new Error(
          `Synthetic control fixture did not become ready (exit ${child.exitCode ?? "running"}): ${errors}`,
          { cause: reason },
        );
      }
      await use(installation);
    } finally {
      if (child.exitCode === null) child.kill("SIGTERM");
      await closed;
    }
  },
});
const PASSWORD = "Synthetic-only-browser-passphrase-478";
function totp(secret: string, offset = 0) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const char of secret.replace(/=/g, ""))
    bits += alphabet.indexOf(char).toString(2).padStart(5, "0");
  const bytes = [];
  for (let i = 0; i + 8 <= bits.length; i += 8)
    bytes.push(parseInt(bits.slice(i, i + 8), 2));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000) + offset));
  const digest = createHmac("sha1", Buffer.from(bytes))
      .update(counter)
      .digest(),
    at = digest[digest.length - 1] & 15;
  return ((digest.readUInt32BE(at) & 0x7fffffff) % 1e6)
    .toString()
    .padStart(6, "0");
}
async function verify(page: Page, secret: string, offset = 0) {
  await page.getByLabel(/^Authenticator code/).fill(totp(secret, offset));
  await page
    .getByRole("button", { name: "Verify and continue", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Overview", exact: true }),
  ).toBeVisible();
}
async function bootstrap(page: Page, installation: Installation) {
  // No operating-system clipboard is touched by synthetic credential tests.
  await page.addInitScript(() =>
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: async () => undefined },
    }),
  );
  await page.goto(installation.url);
  await page
    .getByRole("button", { name: "Set up your workspace", exact: true })
    .click();
  await page
    .getByLabel("Private setup token", { exact: true })
    .fill(installation.bootstrapToken);
  await page
    .getByLabel("Pharmacy group name", { exact: true })
    .fill("Synthetic Browser Pharmacy Group");
  await page
    .getByLabel("Your full name", { exact: true })
    .fill("Synthetic Browser Owner");
  await page
    .getByLabel("Work email", { exact: true })
    .fill("browser.owner@example.test");
  await page.getByLabel(/^Create a password/).fill(PASSWORD);
  const response = page.waitForResponse((r) =>
    r.url().endsWith("/setup/begin"),
  );
  await page
    .getByRole("button", { name: "Set up authenticator", exact: true })
    .click();
  const challenge = await (await response).json();
  expect(challenge.totp_secret).toBeTruthy();
  expect(challenge.totp_uri).toMatch(
    /^otpauth:\/\/totp\/AisleSignals(?:%3A|:)/,
  );
  await expect(
    page.getByRole("img", { name: "Authenticator setup QR code" }),
  ).toBeVisible();
  await expect(
    page.getByText("Scan with your authenticator app"),
  ).toBeVisible();
  expect(
    (
      await page.request.get(installation.url + "/control-api/session")
    ).status(),
  ).toBe(401);
  // Copy remains an explicit clipboard action even when the MFA form is valid.
  await page
    .getByLabel(/^Authenticator code/)
    .fill(totp(challenge.totp_secret));
  const copy = page.getByRole("button", { name: "Copy securely", exact: true });
  await expect(copy).toHaveAttribute("type", "button");
  await copy.click();
  await expect(
    page.getByRole("button", { name: "Copied", exact: true }),
  ).toBeVisible();
  expect(
    (
      await page.request.get(installation.url + "/control-api/session")
    ).status(),
  ).toBe(401);
  await verify(page, challenge.totp_secret);
  await expect(
    page.getByRole("img", { name: "Authenticator setup QR code" }),
  ).toHaveCount(0);
  return challenge.totp_secret as string;
}
async function navigate(page: Page, name: string) {
  const menu = page.getByRole("button", { name: /open navigation|open menu/i });
  if (await menu.isVisible()) await menu.click();
  await page.getByRole("button", { name, exact: true }).click();
}
async function addPharmacy(page: Page, name: string) {
  await navigate(page, "Pharmacies");
  await page.getByRole("button", { name: "Add pharmacy", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Pharmacy name", { exact: true }).fill(name);
  await dialog
    .getByLabel(/^Address/)
    .fill("Synthetic address for browser verification");
  const response = page.waitForResponse(
    (r) =>
      r.url().endsWith("/control-api/pharmacies") &&
      r.request().method() === "POST",
  );
  await dialog
    .getByRole("button", { name: "Add pharmacy", exact: true })
    .click();
  const value = await (await response).json();
  await expect(dialog).toHaveCount(0);
  return value;
}
async function api(
  request: APIRequestContext,
  installation: Installation,
  path: string,
  body: unknown,
  method = "POST",
) {
  const session = await (
    await request.get(installation.url + "/control-api/session")
  ).json();
  return request.fetch(installation.url + "/control-api" + path, {
    method,
    data: body,
    headers: { Origin: installation.url, "X-CSRF-Token": session.csrf_token },
  });
}
async function createInvitation(
  page: Page,
  name: string,
  email: string,
  branch: string,
  role = "REVIEWER",
) {
  await navigate(page, "Team & access");
  await page
    .getByRole("button", { name: "Invite team member", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Full name", { exact: true }).fill(name);
  await dialog.getByLabel("Email address", { exact: true }).fill(email);
  await dialog.getByLabel(/^Workspace role/).selectOption(role);
  await dialog.getByLabel(branch, { exact: true }).check();
  const response = page.waitForResponse(
    (r) => r.url().endsWith("/invitations") && r.request().method() === "POST",
  );
  await dialog
    .getByRole("button", { name: "Create invitation", exact: true })
    .click();
  const invitation = await (await response).json();
  await expect(dialog).toContainText("No email has been sent");
  await dialog.getByRole("button", { name: "Done", exact: true }).click();
  return invitation;
}
async function enrolLaptop(
  page: Page,
  installation: Installation,
  branch: any,
) {
  await navigate(page, "Laptops");
  await page
    .getByRole("button", { name: "Connect a laptop", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog
    .getByRole("combobox", { name: "Pharmacy", exact: true })
    .selectOption(branch.id);
  await dialog.getByLabel(/^Laptop name/).fill("Synthetic Harbour MacBook");
  await dialog
    .getByLabel("Operating system", { exact: true })
    .selectOption("MACOS");
  const response = page.waitForResponse((r) =>
    r.url().endsWith("/devices/enrolments"),
  );
  await dialog
    .getByRole("button", { name: "Create connection code", exact: true })
    .click();
  const code = await (await response).json();
  await expect(dialog).toContainText("does not select a camera");
  await dialog.getByRole("button", { name: "Done", exact: true }).click();
  const enrolled = await page.request.post(
    installation.url + "/device-api/enrol",
    {
      data: {
        token: code.token,
        name: "Synthetic Harbour MacBook",
        platform: "MACOS",
        app_version: "synthetic-browser-0.1",
      },
    },
  );
  expect(enrolled.status()).toBe(201);
  return await enrolled.json();
}
async function ingest(
  page: Page,
  installation: Installation,
  laptop: any,
  historical = false,
) {
  const response = await page.request.post(
    installation.url + "/device-api/alerts",
    {
      headers: { Authorization: "Bearer " + laptop.device_token },
      data: {
        source_event_id: crypto.randomUUID(),
        event_code: "POSSIBLE_CONCEALMENT",
        source_label: "Synthetic camera 2",
        occurred_at: new Date(
          Date.now() - (historical ? 300000 : 0),
        ).toISOString(),
        historical,
      },
    },
  );
  expect(response.status()).toBe(201);
  return response.json();
}

test("real first-owner setup, MFA login and scoped invitation remain secret-free across logout", async ({
  page,
  installation,
  browser,
}) => {
  const secret = await bootstrap(page, installation);
  const north = await addPharmacy(page, "Synthetic Harbour");
  await addPharmacy(page, "Synthetic South");
  const invitation = await createInvitation(
    page,
    "Synthetic Reviewer",
    "browser.reviewer@example.test",
    north.name,
  );
  const other = await browser.newContext();
  const invite = await other.newPage();
  await invite.goto(
    installation.url + "/#accept-invite?token=" + invitation.token,
  );
  await expect(
    invite.getByRole("heading", { name: "Welcome to your team", exact: true }),
  ).toBeVisible();
  expect(invite.url()).not.toContain(invitation.token);
  await invite
    .getByLabel("Your full name", { exact: true })
    .fill("Synthetic Reviewer");
  await invite.getByLabel(/^Create a password/).fill(PASSWORD);
  const pending = invite.waitForResponse((r) =>
    r.url().endsWith("/invitations/begin"),
  );
  await invite
    .getByRole("button", { name: "Set up authenticator", exact: true })
    .click();
  const challenge = await (await pending).json();
  await expect(
    invite.getByRole("img", { name: "Authenticator setup QR code" }),
  ).toBeVisible();
  await verify(invite, challenge.totp_secret);
  await navigate(invite, "Pharmacies");
  await expect(invite.locator("main")).toContainText(north.name);
  await expect(invite.locator("main")).not.toContainText("Synthetic South");
  await expect(
    invite.getByRole("button", { name: "Team & access", exact: true }),
  ).toHaveCount(0);
  expect(
    (
      await invite.request.get(installation.url + "/control-api/users")
    ).status(),
  ).toBe(403);
  await other.close();
  await page.getByRole("button", { name: /sign out/i }).click();
  await expect(
    page.getByRole("heading", { name: "Welcome back", exact: true }),
  ).toBeVisible();
  await expect(page.locator("body")).not.toContainText("Synthetic Harbour");
  await page
    .getByLabel("Work email", { exact: true })
    .fill("browser.owner@example.test");
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page
    .getByRole("button", { name: "Continue securely", exact: true })
    .click();
  expect(
    (
      await page.request.get(installation.url + "/control-api/session")
    ).status(),
  ).toBe(401);
  await expect(
    page.getByRole("img", { name: "Authenticator setup QR code" }),
  ).toHaveCount(0);
  await verify(page, secret, 1);
  const state = await page.evaluate(() => ({
    local: Object.keys(localStorage),
    session: Object.keys(sessionStorage),
    cookies: document.cookie,
    url: location.href,
  }));
  expect(state.local).toEqual([]);
  expect(state.session).toEqual([]);
  expect(state.cookies).not.toContain("session");
  for (const value of [
    PASSWORD,
    secret,
    invitation.token,
    installation.bootstrapToken,
  ])
    expect(JSON.stringify(state)).not.toContain(value);
});

test("owner connects a laptop, reviews actual received metadata into a case and revokes its credential", async ({
  page,
  installation,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await bootstrap(page, installation);
  const branch = await addPharmacy(page, "Synthetic Harbour");
  const laptop = await enrolLaptop(page, installation, branch);
  const heartbeat = await page.request.post(
    installation.url + "/device-api/heartbeat",
    {
      headers: { Authorization: "Bearer " + laptop.device_token },
      data: {
        sequence: 0,
        monitoring_status: "ACTIVE",
        camera_count: 4,
        app_version: "synthetic-browser-0.1",
      },
    },
  );
  expect(heartbeat.status()).toBe(200);
  const alert = await ingest(page, installation, laptop, true);
  await navigate(page, "Alerts");
  const row = page.getByRole("row").filter({ hasText: "Synthetic camera 2" });
  await expect(row).toContainText("Synced history");
  await row
    .getByRole("button", { name: "Open record details", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog
    .getByRole("button", { name: "Acknowledge attention", exact: true })
    .click();
  await expect(dialog).toContainText("Acknowledged");
  await dialog
    .getByLabel("Staff outcome", { exact: true })
    .selectOption("UNCLEAR");
  await dialog
    .getByLabel(/^Review notes/)
    .fill(
      "Synthetic staff inspection: evidence is insufficient; follow-up required.",
    );
  await dialog
    .getByLabel("Create an incident for staff follow-up", { exact: true })
    .check();
  await dialog
    .getByLabel("Incident title", { exact: true })
    .fill("Synthetic Harbour follow-up");
  const receipt = page.waitForResponse((r) =>
    r.url().endsWith(`/alerts/${alert.id}/review`),
  );
  await dialog
    .getByRole("button", { name: "Save review", exact: true })
    .click();
  expect((await receipt).status()).toBe(200);
  await expect(dialog).toContainText("Staff review recorded");
  await page.keyboard.press("Escape");
  await navigate(page, "Incidents");
  const incident = page
    .getByRole("row")
    .filter({ hasText: "Synthetic Harbour follow-up" });
  await incident
    .getByRole("button", { name: "Open record details", exact: true })
    .click();
  await page
    .getByRole("dialog")
    .getByLabel("Case status", { exact: true })
    .selectOption("CLOSED");
  await page.getByRole("button", { name: "Save case", exact: true }).click();
  await expect(incident).toContainText("Closed");
  await navigate(page, "Laptops");
  const deviceRow = page
    .getByRole("row")
    .filter({ hasText: "Synthetic Harbour MacBook" });
  await expect(deviceRow).toContainText("Online");
  await deviceRow
    .getByRole("button", { name: "Open record details", exact: true })
    .click();
  await page
    .getByLabel("I intend to disconnect this laptop.", { exact: true })
    .check();
  await page
    .getByRole("button", { name: "Revoke connection", exact: true })
    .click();
  await expect(deviceRow).toContainText("Revoked");
  expect(
    (
      await page.request.post(installation.url + "/device-api/heartbeat", {
        headers: { Authorization: "Bearer " + laptop.device_token },
        data: {
          sequence: 1,
          monitoring_status: "ACTIVE",
          camera_count: 4,
          app_version: "synthetic",
        },
      })
    ).status(),
  ).toBe(401);
  expect(errors).toEqual([]);
  await page.screenshot({
    path: ".local/control-operations-desktop.png",
    fullPage: true,
  });
});

test("withdrawn laptop source clears cached details and preserves the reviewed case", async ({
  page,
  installation,
}) => {
  await bootstrap(page, installation);
  const branch = await addPharmacy(page, "Synthetic Harbour");
  const laptop = await enrolLaptop(page, installation, branch);
  const sourceId = crypto.randomUUID();
  const response = await page.request.post(
    installation.url + "/device-api/sync/v1/observations",
    {
      headers: { Authorization: "Bearer " + laptop.device_token },
      data: {
        source_event_id: sourceId,
        event_code: "POSSIBLE_CONCEALMENT",
        source_label: "Synthetic lifecycle camera",
        occurred_at: new Date().toISOString(),
        historical: true,
        expires_at: new Date(Date.now() + 3600000).toISOString(),
      },
    },
  );
  expect(response.status()).toBe(201);
  const alert = await response.json();
  await navigate(page, "Alerts");
  const row = page
    .getByRole("row")
    .filter({ hasText: "Synthetic lifecycle camera" });
  await expect(row).toContainText("Laptop clock");
  await row
    .getByRole("button", { name: "Open record details", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText(
    "Reported observation time — laptop clock",
  );
  await dialog
    .getByLabel(/^Review notes/)
    .fill("Synthetic independent staff notes retained for follow-up.");
  await dialog
    .getByLabel("Create an incident for staff follow-up", { exact: true })
    .check();
  await dialog
    .getByLabel("Incident title", { exact: true })
    .fill("Synthetic retained case");
  await dialog
    .getByRole("button", { name: "Save review", exact: true })
    .click();
  await expect(dialog).toContainText("Staff review recorded");
  const withdrawn = await page.request.post(
    installation.url + "/device-api/sync/v1/withdrawals",
    {
      headers: { Authorization: "Bearer " + laptop.device_token },
      data: { source_event_id: sourceId, reason: "LOCAL_DELETED" },
    },
  );
  expect(withdrawn.status()).toBe(200);
  const refresh = page.waitForResponse(
    (r) => r.url().endsWith("/alerts/" + alert.id) && r.status() === 404,
  );
  // Actual visibility refresh; no mocked app data or server response.
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  await refresh;
  await expect(dialog).toContainText("This record is no longer available");
  await expect(dialog).not.toContainText("Synthetic lifecycle camera");
  await expect(
    dialog.getByRole("button", { name: "Save review", exact: true }),
  ).toHaveCount(0);
  await page.keyboard.press("Escape");
  await navigate(page, "Incidents");
  await page
    .getByRole("row")
    .filter({ hasText: "Synthetic retained case" })
    .getByRole("button", { name: "Open record details", exact: true })
    .click();
  await expect(dialog).toContainText(
    "source observation has expired or been withdrawn",
  );
  await expect(dialog.getByLabel(/^Case notes/)).toHaveValue(
    "Synthetic independent staff notes retained for follow-up.",
  );
});

test("cached laptop source expires offline and removes the unsaved review form", async ({
  page,
  context,
  installation,
}) => {
  await bootstrap(page, installation);
  const branch = await addPharmacy(page, "Synthetic Harbour");
  const laptop = await enrolLaptop(page, installation, branch);
  // Use the real server's accepted deadline while advancing only the browser clock.
  const now = Date.now();
  const response = await page.request.post(
    installation.url + "/device-api/sync/v1/observations",
    {
      headers: { Authorization: "Bearer " + laptop.device_token },
      data: {
        source_event_id: crypto.randomUUID(),
        event_code: "POSSIBLE_CONCEALMENT",
        source_label: "Synthetic expiring camera",
        occurred_at: new Date(now).toISOString(),
        historical: true,
        expires_at: new Date(now + 120000).toISOString(),
      },
    },
  );
  expect(response.status()).toBe(201);
  await page.clock.install({ time: new Date(now) });
  await navigate(page, "Alerts");
  await page
    .getByRole("row")
    .filter({ hasText: "Synthetic expiring camera" })
    .getByRole("button", { name: "Open record details", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel(/^Review notes/).fill("Synthetic temporary draft");
  await context.setOffline(true);
  await page.clock.fastForward(120001);
  await expect(dialog).toContainText("Source observation expired");
  await expect(dialog).not.toContainText("Synthetic expiring camera");
  await expect(dialog.getByLabel(/^Review notes/)).toHaveCount(0);
  await expect(
    dialog.getByRole("button", { name: "Save review", exact: true }),
  ).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("row").filter({ hasText: "Synthetic expiring camera" }),
  ).toHaveCount(0);
  await context.setOffline(false);
});

test("stale staff review conflicts and scope changes discard unsaved notes", async ({
  page,
  installation,
}) => {
  await bootstrap(page, installation);
  const north = await addPharmacy(page, "Synthetic Harbour");
  const south = await addPharmacy(page, "Synthetic South");
  const laptop = await enrolLaptop(page, installation, north);
  const alert = await ingest(page, installation, laptop);
  await navigate(page, "Alerts");
  await page
    .getByRole("row")
    .filter({ hasText: "Synthetic camera 2" })
    .getByRole("button", { name: "Open record details", exact: true })
    .click();
  await page.getByLabel(/^Review notes/).fill("Synthetic stale draft");
  expect(
    (
      await api(page.request, installation, `/alerts/${alert.id}/acknowledge`, {
        expected_version: 1,
      })
    ).status(),
  ).toBe(200);
  await page.getByRole("button", { name: "Save review", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(/changed|refresh/i);
  expect(
    await (
      await page.request.get(installation.url + "/control-api/incidents")
    ).json(),
  ).toEqual({ items: [] });
  await page.keyboard.press("Escape");
  await page
    .getByRole("row")
    .filter({ hasText: "Synthetic camera 2" })
    .getByRole("button", { name: "Open record details", exact: true })
    .click();
  await page.getByLabel(/^Review notes/).fill("Synthetic abandoned draft");
  await page.keyboard.press("Escape");
  const selects = page
    .locator("select")
    .filter({ has: page.locator(`option[value="${south.id}"]`) });
  await selects.first().selectOption(south.id);
  await expect(
    page.getByRole("row").filter({ hasText: "Synthetic camera 2" }),
  ).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText(
    "Synthetic abandoned draft",
  );
});

test("optional evidence is accessible across ready, pending, partial, expired, deleted and access-failure states without bypassing staff review", async ({
  page,
  installation,
}) => {
  await bootstrap(page, installation);
  const branch = await addPharmacy(page, "Synthetic Evidence Pharmacy");
  const laptop = await enrolLaptop(page, installation, branch);
  const alert = await ingest(page, installation, laptop);
  type Mode =
    "READY" | "PENDING" | "PARTIAL" | "EXPIRED" | "DELETED" | "DENIED";
  let mode: Mode = "READY";
  const future = new Date(Date.now() + 3600000).toISOString();
  const past = new Date(Date.now() - 1000).toISOString();
  const snapshot = (state: string, suffix: string, expires_at = future) => ({
    id: `11111111-1111-4111-8111-${suffix.padStart(12, "0")}`,
    kind: "OVERVIEW",
    content_type: "image/png",
    byte_count: 68,
    state,
    expires_at,
  });
  const contract = () => {
    if (mode === "PENDING")
      return {
        evidence_state: "PENDING",
        evidence: [snapshot("PENDING", "2")],
      };
    if (mode === "PARTIAL")
      return {
        evidence_state: "PARTIAL",
        evidence: [
          snapshot("READY", "3"),
          {
            id: "22222222-2222-4222-8222-000000000004",
            kind: "CLIP",
            content_type: "video/mp4",
            byte_count: 4096,
            state: "PENDING",
            expires_at: future,
          },
        ],
      };
    if (mode === "EXPIRED")
      return {
        evidence_state: "EXPIRED",
        evidence: [snapshot("EXPIRED", "5", past)],
      };
    if (mode === "DELETED")
      return {
        evidence_state: "PARTIAL",
        evidence: [snapshot("DELETED", "6")],
      };
    return {
      evidence_state: "READY",
      evidence: [snapshot("READY", mode === "DENIED" ? "7" : "1")],
    };
  };
  await page.route(
    `${installation.url}/control-api/alerts/${alert.id}`,
    async (route) => {
      const original = await route.fetch();
      const detail = await original.json();
      await route.fulfill({
        status: original.status(),
        contentType: "application/json",
        headers: { "Cache-Control": "no-store" },
        body: JSON.stringify({ ...detail, ...contract() }),
      });
    },
  );
  const onePixelPng = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
    "base64",
  );
  await page.route(
    `${installation.url}/control-api/alerts/${alert.id}/evidence/*`,
    async (route) => {
      if (mode === "DENIED") {
        await route.fulfill({ status: 403, body: "" });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "image/png",
        headers: { "Cache-Control": "no-store" },
        body: onePixelPng,
      });
    },
  );
  await navigate(page, "Alerts");
  const row = page.getByRole("row").filter({ hasText: "Synthetic camera 2" });
  const open = async () => {
    await row
      .getByRole("button", { name: "Open record details", exact: true })
      .click();
    return page.getByRole("dialog");
  };
  const close = async () => {
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Close details", exact: true })
      .click();
  };

  let dialog = await open();
  await expect(
    dialog.getByRole("img", {
      name: "Authorised CCTV snapshot for staff review",
      exact: true,
    }),
  ).toBeVisible();
  await expect(dialog).toContainText("Contains personal data");
  await close();

  mode = "PENDING";
  dialog = await open();
  await expect(dialog).toContainText("Evidence is still transferring");
  await expect(dialog).toContainText("Transfer pending");
  await close();

  mode = "PARTIAL";
  dialog = await open();
  await expect(dialog).toContainText("Some evidence is available");
  await expect(
    dialog.getByRole("heading", { name: "Overview snapshot" }),
  ).toBeVisible();
  await expect(
    dialog.getByRole("heading", { name: "Short evidence clip" }),
  ).toBeVisible();
  await close();

  mode = "EXPIRED";
  dialog = await open();
  await expect(dialog).toContainText(
    "Evidence expired and is no longer available",
  );
  await close();

  mode = "DELETED";
  dialog = await open();
  await expect(dialog).toContainText(
    "Evidence was deleted. The review record is unchanged.",
  );
  await close();

  mode = "DENIED";
  dialog = await open();
  await expect(dialog.getByRole("alert")).toContainText(
    "Evidence could not be loaded",
  );
  await expect(
    dialog.getByRole("button", { name: "Retry evidence", exact: true }),
  ).toBeVisible();
  mode = "READY";
  await dialog
    .getByRole("button", { name: "Retry evidence", exact: true })
    .click();
  await expect(
    dialog.getByRole("img", {
      name: "Authorised CCTV snapshot for staff review",
      exact: true,
    }),
  ).toBeVisible();
  await dialog
    .getByLabel("Staff outcome", { exact: true })
    .selectOption("UNCLEAR");
  await dialog
    .getByLabel(/^Review notes/)
    .fill("Synthetic review based on authorised evidence and circumstances.");
  const review = page.waitForResponse((response) =>
    response.url().endsWith(`/alerts/${alert.id}/review`),
  );
  await dialog
    .getByRole("button", { name: "Save review", exact: true })
    .click();
  expect((await review).status()).toBe(200);
  await expect(dialog).toContainText("Staff review recorded");
  await expect(dialog).toContainText("No case created");
});

test("management authentication and drawers are accessible by keyboard and on a phone", async ({
  page,
  installation,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(installation.url);
  await checkAccessibility(page);
  await bootstrap(page, installation);
  await addPharmacy(page, "Synthetic Harbour");
  await page.getByRole("button", { name: "Add pharmacy", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveAccessibleName("Add a pharmacy");
  await checkAccessibility(page, "dialog");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect
    .soft(page.getByRole("button", { name: "Add pharmacy", exact: true }))
    .toBeFocused();
  const overflow = await page.evaluate(() => ({
    viewport: window.innerWidth,
    width: document.documentElement.scrollWidth,
    outside: [...document.querySelectorAll<HTMLElement>("body *")]
      .filter((el) => el.getBoundingClientRect().right > window.innerWidth + 1)
      .map((el) => ({
        tag: el.tagName,
        className: el.className,
        right: el.getBoundingClientRect().right,
      }))
      .slice(0, 20),
  }));
  expect
    .soft(overflow.width, JSON.stringify(overflow))
    .toBeLessThanOrEqual(overflow.viewport + 1);
  await checkAccessibility(page);
  await page.screenshot({ path: ".local/control-mobile.png", fullPage: true });
});

async function checkAccessibility(page: Page, selector?: string) {
  const builder = new AxeBuilder({ page });
  const result = await (
    selector ? builder.include(selector) : builder
  ).analyze();
  expect
    .soft(
      result.violations.map((violation) => ({
        id: violation.id,
        nodes: violation.nodes.map((node) => ({
          target: node.target,
          failure: node.failureSummary,
        })),
      })),
    )
    .toEqual([]);
}

test("offline idle lock clears sensitive views and survives reload without restoring the cookie session", async ({
  page,
  installation,
}) => {
  await page.clock.install();
  await bootstrap(page, installation);
  await addPharmacy(page, "Synthetic Harbour");
  await page.getByRole("button", { name: "Add pharmacy", exact: true }).click();
  await page
    .getByLabel("Pharmacy name", { exact: true })
    .fill("Synthetic unsaved pharmacy");
  // Only the transport failure is simulated; the cookie/session remains real.
  await page.route("**/control-api/logout", (route) =>
    route.abort("internetdisconnected"),
  );
  await page.clock.fastForward(15 * 60 * 1000 + 1000);
  await expect(
    page.getByRole("heading", { name: "Welcome back", exact: true }),
  ).toBeVisible();
  await expect(page.locator("body")).not.toContainText("Synthetic Harbour");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(
    (
      await page.request.get(installation.url + "/control-api/session")
    ).status(),
  ).toBe(200);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Welcome back", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Overview", exact: true }),
  ).toHaveCount(0);
  expect(await page.evaluate(() => ({ ...sessionStorage }))).toEqual({
    "aislesignals-control-locked": "1",
  });
});

test("another tab signing out discards open account records and pending form contents", async ({
  page,
  installation,
  context,
}) => {
  await bootstrap(page, installation);
  await addPharmacy(page, "Synthetic Harbour");
  const sibling = await context.newPage();
  await sibling.goto(installation.url);
  await navigate(sibling, "Pharmacies");
  await expect(sibling.locator("main")).toContainText("Synthetic Harbour");
  await sibling
    .getByRole("button", { name: "Add pharmacy", exact: true })
    .click();
  await sibling
    .getByLabel("Pharmacy name", { exact: true })
    .fill("Synthetic sibling draft");
  await page.getByRole("button", { name: /sign out/i }).click();
  await expect(
    sibling.getByRole("heading", { name: "Welcome back", exact: true }),
  ).toBeVisible();
  await expect(sibling.getByRole("dialog")).toHaveCount(0);
  await expect(sibling.locator("body")).not.toContainText("Synthetic Harbour");
  await sibling.reload();
  await expect(
    sibling.getByRole("heading", { name: "Welcome back", exact: true }),
  ).toBeVisible();
  expect(
    (
      await sibling.request.get(installation.url + "/control-api/session")
    ).status(),
  ).toBe(401);
  await sibling.close();
});

test("Windows connection setup offers the complete tool and creates a scoped device", async ({
  page,
  installation,
}) => {
  await bootstrap(page, installation);
  const branch = await addPharmacy(page, "Synthetic Windows Branch");
  await navigate(page, "Laptops");
  await page
    .getByRole("button", { name: "Connect a laptop", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog
    .getByRole("combobox", { name: "Pharmacy", exact: true })
    .selectOption(branch.id);
  await dialog.getByLabel(/^Laptop name/).fill("Synthetic Windows laptop");
  await dialog
    .getByLabel("Operating system", { exact: true })
    .selectOption("WINDOWS");
  const create = dialog.getByRole("button", {
    name: "Create connection code",
    exact: true,
  });
  await expect(create).toBeEnabled();
  const issued = page.waitForResponse((response) =>
    response.url().endsWith("/devices/enrolments"),
  );
  await create.click();
  const code = await (await issued).json();
  await expect(dialog).toContainText("PowerShell");
  await expect(dialog).toContainText("py -3 cloud_companion.py enrol");
  await expect(dialog.locator(".command-panel code")).toContainText(
    "--name 'Synthetic Windows laptop'",
  );
  await expect(dialog).toContainText(
    "paste the code shown above and press Enter",
  );
  await expect(dialog).toContainText("does not select a camera");
  const link = dialog.getByRole("link", {
    name: "Download the connection tool",
    exact: true,
  });
  await expect(link).toHaveAttribute("href", "/downloads/cloud-companion.zip");
  const download = await page.request.get(
    installation.url + "/downloads/cloud-companion.zip",
  );
  expect(download.status()).toBe(200);
  expect(download.headers()["content-type"]).toBe("application/zip");
  expect((await download.body()).subarray(0, 4).toString("hex")).toBe(
    "504b0304",
  );
  const device = await page.request.post(
    installation.url + "/device-api/enrol",
    {
      data: {
        token: code.token,
        name: "Synthetic Windows laptop",
        platform: "WINDOWS",
        app_version: "synthetic-windows-ui",
      },
    },
  );
  expect(device.status()).toBe(201);
  await dialog.getByRole("button", { name: "Done", exact: true }).click();
  await navigate(page, "Overview");
  await navigate(page, "Laptops");
  const row = page
    .getByRole("row")
    .filter({ hasText: "Synthetic Windows laptop" });
  await expect(row).toContainText("Windows");
  await expect(row).toContainText("Synthetic Windows Branch");
});

test("download centre exposes the verified connection utility and secure pairing route", async ({
  page,
  installation,
}) => {
  await bootstrap(page, installation);
  await navigate(page, "Downloads");

  await expect(
    page.getByRole("heading", { name: "Download AisleSignals", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(
      "Connection utility available now — this is not an installer",
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("link", {
      name: "Download macOS pilot (.dmg)",
      exact: true,
    }),
  ).toHaveAttribute(
    "href",
    "https://github.com/g784dwcd2r-crypto/AisleSignals/releases/download/pilot-macos-v0.1.0/AisleSignalsPilot-macOS-arm64-v0.1.0-unsigned.dmg",
  );
  await expect(
    page.getByRole("heading", {
      name: "Desktop installation files are not released yet",
      exact: true,
    }),
  ).toBeVisible();

  const macDownload = page.getByRole("link", {
    name: "Download macOS connection utility ZIP",
    exact: true,
  });
  const windowsDownload = page.getByRole("link", {
    name: "Download Windows connection utility ZIP",
    exact: true,
  });
  await expect(macDownload).toHaveAttribute(
    "href",
    "/downloads/cloud-companion.zip",
  );
  await expect(windowsDownload).toHaveAttribute(
    "href",
    "/downloads/cloud-companion.zip",
  );
  const archive = await page.request.get(
    installation.url + "/downloads/cloud-companion.zip",
  );
  expect(archive.status()).toBe(200);
  expect(archive.headers()["content-type"]).toBe("application/zip");
  expect((await archive.body()).subarray(0, 4).toString("hex")).toBe(
    "504b0304",
  );
  await checkAccessibility(page);

  await page
    .getByRole("button", { name: "Pair a laptop", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Connected where it matters" }),
  ).toBeVisible();
});
