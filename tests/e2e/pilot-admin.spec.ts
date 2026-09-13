import { test as base, expect, type Page } from "@playwright/test";
import { spawn } from "node:child_process";
import { mkdtemp, rm, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";

// Real isolated API, fresh synthetic acceptance data, no preview or customer accounts.
const serverCode = String.raw`
import json, os, socket, sys
from pathlib import Path
for key in list(os.environ):
    if key.startswith('AISLESIGNALS_'): del os.environ[key]
listener = socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen(128)
port = listener.getsockname()[1]
unavailable = socket.socket(); unavailable.bind(('127.0.0.1',0))
os.environ['AISLESIGNALS_MODE']='pilot'
os.environ['AISLESIGNALS_PORT']=str(port)
os.environ['AISLESIGNALS_DB_PATH']=str(Path(sys.argv[1]).resolve() / 'pilot.db')
os.environ['AISLESIGNALS_WEB_DIST']=str(Path.cwd() / 'apps/web/dist')
os.environ['AISLESIGNALS_VISION_URL']='http://127.0.0.1:'+str(unavailable.getsockname()[1])
from services.api.store import Store
from services.api.pilot_admin import issue_setup_token
store=Store(os.environ['AISLESIGNALS_DB_PATH'],mode='pilot')
code=issue_setup_token(store)['token']
from services.api.app import app
import uvicorn
print('ADMIN_READY '+json.dumps({'url':'http://127.0.0.1:'+str(port),'code':code}),flush=True)
uvicorn.Server(uvicorn.Config(app,log_level='warning',access_log=False,proxy_headers=False)).run(sockets=[listener])
`;
type Installation = { url: string; code: string };
const test = base.extend<{ installation: Installation }>({
  installation: async ({}, use) => {
    const directory = await mkdtemp(
      join(tmpdir(), "aislesignals-admin-browser-"),
    );
    const child = spawn(
      process.env.AISLESIGNALS_TEST_PYTHON || "python",
      ["-u", "-c", serverCode, directory],
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
            .find((value) => value.startsWith("ADMIN_READY "));
          if (line) resolve(JSON.parse(line.slice("ADMIN_READY ".length)));
        });
        child.stderr.on("data", (chunk) => {
          errors += String(chunk);
        });
        child.once("error", reject);
        child.once("exit", (code) =>
          reject(new Error(`Isolated admin API exited (${code}): ${errors}`)),
        );
      });
      const installation = await Promise.race([
        ready,
        new Promise<never>((_, reject) => {
          timer = setTimeout(
            () => reject(new Error("Isolated admin API did not start")),
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

const ownerPassword = "Synthetic owner acceptance passphrase 73!";
const staffPassword = "Synthetic staff acceptance passphrase 84!";
const resetPassword = "Synthetic reset acceptance passphrase 95!";

async function ownerSetup(
  page: Page,
  installation: Installation,
  rejectCode = false,
) {
  await page.goto(installation.url);
  await expect(
    page.getByLabel("Owner email address", { exact: true }),
  ).toBeVisible();
  if (rejectCode) {
    await mkdir(resolve(".local/release-evidence"), { recursive: true });
    await page.screenshot({
      path: resolve(".local/release-evidence/first-owner-setup.png"),
      fullPage: true,
    });
  }
  await page
    .getByLabel("Pharmacy group name", { exact: true })
    .fill("Synthetic Acceptance Group");
  await page
    .getByLabel("First pharmacy branch", { exact: true })
    .fill("Synthetic North Branch");
  await page
    .getByLabel("Owner full name", { exact: true })
    .fill("Synthetic Owner");
  await page
    .getByLabel("Owner email address", { exact: true })
    .fill("owner@example.test");
  async function secrets(code: string) {
    await page
      .getByLabel("Owner passphrase", { exact: true })
      .fill(ownerPassword);
    await page
      .getByLabel("Confirm owner passphrase", { exact: true })
      .fill(ownerPassword);
    await page.getByLabel("One-time setup code", { exact: true }).fill(code);
  }
  if (rejectCode) {
    await secrets("invalid-synthetic-test-code");
    await page
      .getByRole("button", { name: "Create owner account", exact: true })
      .click();
    await expect(
      page.getByText(/The setup code is invalid or expired/),
    ).toBeVisible();
    await expect(
      page.getByLabel("Owner passphrase", { exact: true }),
    ).toHaveValue("");
    await expect(
      page.getByLabel("One-time setup code", { exact: true }),
    ).toHaveValue("");
  }
  await secrets(installation.code);
  await page
    .getByRole("button", { name: "Create owner account", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Sign in", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("Password", { exact: true })).toHaveValue("");
  expect(
    (await page.request.get(`${installation.url}/api/session`)).status(),
  ).toBe(401);
  await login(page, installation.url, "owner@example.test", ownerPassword);
  await page
    .getByRole("button", { name: "Administration", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Branches you manage", exact: true }),
  ).toBeVisible();
}
async function login(page: Page, url: string, email: string, password: string) {
  await page.goto(url);
  await page.getByLabel("Email address", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByLabel("Active pharmacy branch")).toBeVisible();
}
async function createStaff(page: Page) {
  await page.getByRole("button", { name: "Add user", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog
    .getByLabel("Full name", { exact: true })
    .fill("Synthetic Reviewer");
  await dialog
    .getByLabel("Email address", { exact: true })
    .fill("reviewer@example.test");
  await dialog
    .getByLabel("New account passphrase", { exact: true })
    .fill(staffPassword);
  await dialog
    .getByLabel("Confirm new account passphrase", { exact: true })
    .fill(staffPassword);
  await dialog
    .getByLabel("Your manager password", { exact: true })
    .fill(ownerPassword);
  await dialog
    .getByRole("button", { name: "Create user", exact: true })
    .click();
  await expect(
    page.getByRole("article", {
      name: "Account Synthetic Reviewer",
      exact: true,
    }),
  ).toBeVisible();
}

test("secure first-owner setup and manager create branch, user, access, disable, enable and reset", async ({
  page,
  browser,
  installation,
}) => {
  test.setTimeout(75000);
  await ownerSetup(page, installation, true);
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
  await page
    .getByRole("button", { name: "Add pharmacy branch", exact: true })
    .click();
  let dialog = page.getByRole("dialog");
  await dialog
    .getByLabel("Pharmacy branch name", { exact: true })
    .fill("Synthetic South Branch");
  await dialog
    .getByLabel("Your manager password", { exact: true })
    .fill("Incorrect synthetic password 55!");
  await dialog
    .getByRole("button", { name: "Create branch", exact: true })
    .click();
  await expect(dialog.getByRole("alert")).toBeVisible();
  await expect(
    dialog.getByLabel("Your manager password", { exact: true }),
  ).toHaveValue("");
  await expect(page.getByLabel("Active pharmacy branch")).toBeVisible();
  await dialog
    .getByLabel("Your manager password", { exact: true })
    .fill(ownerPassword);
  await dialog
    .getByRole("button", { name: "Create branch", exact: true })
    .click();
  await expect(
    page.getByLabel("Active pharmacy branch").locator("option"),
  ).toHaveCount(2);
  await createStaff(page);
  await page.screenshot({
    path: resolve(".local/release-evidence/administration-roster.png"),
    fullPage: true,
  });
  const account = page.getByRole("article", {
    name: "Account Synthetic Reviewer",
    exact: true,
  });
  await account
    .getByRole("button", { name: "Change branch access", exact: true })
    .click();
  dialog = page.getByRole("dialog");
  await dialog
    .getByRole("combobox", { name: "Pharmacy branch", exact: true })
    .selectOption({ label: "Synthetic South Branch" });
  await dialog
    .getByLabel("Your manager password", { exact: true })
    .fill(ownerPassword);
  await dialog
    .getByRole("button", { name: "Save account change", exact: true })
    .click();
  await expect(
    account.getByText("Synthetic South Branch", { exact: true }),
  ).toBeVisible();
  const staffContext = await browser.newContext();
  try {
    const staff = await staffContext.newPage();
    await login(
      staff,
      installation.url,
      "reviewer@example.test",
      staffPassword,
    );
    await expect(
      staff.getByRole("button", { name: "Administration", exact: true }),
    ).toHaveCount(0);
    expect(
      (await staff.request.get(`${installation.url}/api/admin/users`)).status(),
    ).toBe(403);
    await account
      .getByRole("button", { name: "Disable user", exact: true })
      .click();
    dialog = page.getByRole("dialog");
    await dialog
      .getByLabel("Your manager password", { exact: true })
      .fill(ownerPassword);
    await dialog
      .getByRole("button", { name: "Disable account", exact: true })
      .click();
    await expect(account.getByText("Disabled", { exact: true })).toBeVisible();
    expect(
      (await staff.request.get(`${installation.url}/api/session`)).status(),
    ).toBe(401);
    await account
      .getByRole("button", { name: "Enable user", exact: true })
      .click();
    dialog = page.getByRole("dialog");
    await dialog
      .getByLabel("Your manager password", { exact: true })
      .fill(ownerPassword);
    await dialog
      .getByRole("button", { name: "Enable account", exact: true })
      .click();
    await expect(account.getByText("Enabled", { exact: true })).toBeVisible();
    await account
      .getByRole("button", { name: "Reset password", exact: true })
      .click();
    dialog = page.getByRole("dialog");
    await dialog
      .getByLabel("New account passphrase", { exact: true })
      .fill(resetPassword);
    await dialog
      .getByLabel("Confirm new account passphrase", { exact: true })
      .fill(resetPassword);
    await dialog
      .getByLabel("Your manager password", { exact: true })
      .fill(ownerPassword);
    await dialog
      .getByRole("button", { name: "Save account change", exact: true })
      .click();
    await expect(dialog).toHaveCount(0);
    await staff.goto(installation.url);
    const failed = await staff.request.post(`${installation.url}/api/login`, {
      headers: { Origin: installation.url },
      data: { email: "reviewer@example.test", password: staffPassword },
    });
    expect(failed.status()).toBe(401);
    await login(
      staff,
      installation.url,
      "reviewer@example.test",
      resetPassword,
    );
  } finally {
    await staffContext.close();
  }
});

test("cancelled forms discard credentials and stale manager edits reload current account state", async ({
  page,
  installation,
}, testInfo) => {
  await ownerSetup(page, installation);
  await createStaff(page);
  const account = page.getByRole("article", {
    name: "Account Synthetic Reviewer",
    exact: true,
  });
  await account
    .getByRole("button", { name: "Reset password", exact: true })
    .click();
  let dialog = page.getByRole("dialog");
  await dialog
    .getByLabel("New account passphrase", { exact: true })
    .fill("Discarded synthetic passphrase 88!");
  await dialog
    .getByLabel("Your manager password", { exact: true })
    .fill(ownerPassword);
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await account
    .getByRole("button", { name: "Reset password", exact: true })
    .click();
  await expect(
    dialog.getByLabel("New account passphrase", { exact: true }),
  ).toHaveValue("");
  await expect(
    dialog.getByLabel("Your manager password", { exact: true }),
  ).toHaveValue("");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await account
    .getByRole("button", { name: "Change branch access", exact: true })
    .click();
  dialog = page.getByRole("dialog");
  const session = await (
    await page.request.get(`${installation.url}/api/session`)
  ).json();
  const roster = await (
    await page.request.get(`${installation.url}/api/admin/users`)
  ).json();
  const target = roster.users.find(
    (user: { email: string }) => user.email === "reviewer@example.test",
  );
  const changed = await page.request.post(
    `${installation.url}/api/admin/users/${target.id}/enabled`,
    {
      headers: {
        Origin: installation.url,
        "X-CSRF-Token": session.csrf_token,
        "X-AisleSignals-Site": session.current_site_id,
      },
      data: {
        enabled: false,
        manager_password: ownerPassword,
        expected_version: target.version,
      },
    },
  );
  expect(changed.status()).toBe(200);
  await dialog
    .getByRole("combobox", { name: "Branch role", exact: true })
    .selectOption("MANAGER");
  await dialog
    .getByLabel("Your manager password", { exact: true })
    .fill(ownerPassword);
  await dialog
    .getByRole("button", { name: "Save account change", exact: true })
    .click();
  await expect(dialog).toHaveCount(0);
  await expect(
    page.getByText(/This account changed while you were editing/),
  ).toBeVisible();
  await expect(account.getByText("Disabled", { exact: true })).toBeVisible();
  await expect(
    account.locator("li").filter({ hasText: "Synthetic North Branch" }),
  ).toContainText("Reviewer");
  await page.screenshot({
    path: testInfo.outputPath("administration-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    )
    .toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({
    path: testInfo.outputPath("administration-mobile.png"),
    fullPage: true,
  });
});
