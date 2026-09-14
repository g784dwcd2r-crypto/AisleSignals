import { test as base, expect, type Page } from "@playwright/test";
import { spawn } from "node:child_process";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";

// Actual local auth, private storage, SQLite and CloudTransport HTTP. The cloud
// boundary is an owned loopback fixture with synthetic identities, not a tenant.
const server = String.raw`
import json, os, socket, sys
from pathlib import Path
for key in list(os.environ):
    if key.startswith('AISLESIGNALS_'): del os.environ[key]
directory=Path(sys.argv[1]).resolve()
listener=socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen(128)
port=listener.getsockname()[1]
unavailable=socket.socket(); unavailable.bind(('127.0.0.1',0))
os.environ['AISLESIGNALS_MODE']='pilot'
os.environ['AISLESIGNALS_PORT']=str(port)
os.environ['AISLESIGNALS_DB_PATH']=str(directory/'pilot.db')
os.environ['AISLESIGNALS_WEB_DIST']=str(Path.cwd()/'apps/web/dist')
os.environ['AISLESIGNALS_VISION_URL']='http://127.0.0.1:'+str(unavailable.getsockname()[1])
from services.api.app import app
from services.api import cloud_delivery
# This suite exercises actual pairing HTTP. Delivery has separate process/HTTPS
# tests; never let its isolated sender contact this fixture's public-form origin.
class InertDelivery:
    running=False
    def __init__(self,*args,**kwargs): pass
    def start(self): return True
    def stop(self,**kwargs): return True
cloud_delivery.CloudDelivery=InertDelivery
from services.api.pilot_identity import initialise, add_site, add_user, grant_user
from services.api.cloud_transport import CloudTransport
sys.path.insert(0,str(Path.cwd()/'tests/api'))
from test_cloud_connection import Remote
password='Synthetic connection manager passphrase 73!'
initial=initialise(app.state.store,'Synthetic Local Group','Synthetic North','manager@example.test','Synthetic Manager',password)
south=add_site(app.state.store,initial['site']['organisation_id'],'Synthetic South')
grant_user(app.state.store,'manager@example.test',south['id'],'MANAGER')
add_user(app.state.store,'reviewer@example.test','Synthetic Reviewer',password,[initial['site']['id']],'REVIEWER')
remote=Remote()
with remote.serve() as origin:
    def transport(selected):
        remote.identity_status=401 if (directory/'fail-identity').exists() else 200
        return CloudTransport(origin,allow_local_test=True)
    app.state.cloud_connection.transport_factory=transport
    print('CONNECTION_READY '+json.dumps({'url':'http://127.0.0.1:'+str(port),'north':initial['site']['id'],'south':south['id'],'identity':remote.identity}),flush=True)
    import uvicorn
    uvicorn.Server(uvicorn.Config(app,log_level='warning',access_log=False,proxy_headers=False)).run(sockets=[listener])
`;
type Installation = {
  url: string;
  north: string;
  south: string;
  directory: string;
  identity: { device_id: string; organisation_id: string; pharmacy_id: string };
};
const test = base.extend<{ installation: Installation }>({
  installation: async ({}, use) => {
    const directory = await mkdtemp(
      join(tmpdir(), "aislesignals-cloud-browser-"),
    );
    const child = spawn(
      process.env.AISLESIGNALS_TEST_PYTHON || "python",
      ["-u", "-c", server, directory],
      { cwd: process.cwd(), stdio: ["ignore", "pipe", "pipe"] },
    );
    const closed = new Promise<void>((done) =>
      child.once("close", () => done()),
    );
    let output = "",
      errors = "",
      timer: NodeJS.Timeout | undefined;
    try {
      const ready = new Promise<Installation>((done, reject) => {
        child.stdout.on("data", (chunk) => {
          output += String(chunk);
          const line = output
            .split("\n")
            .find((value) => value.startsWith("CONNECTION_READY "));
          if (line) done(JSON.parse(line.slice("CONNECTION_READY ".length)));
        });
        child.stderr.on("data", (chunk) => {
          errors += String(chunk);
        });
        child.once("error", reject);
        child.once("exit", (code) =>
          reject(
            new Error(`Synthetic connection fixture exited ${code}: ${errors}`),
          ),
        );
      });
      const installation = await Promise.race([
        ready,
        new Promise<never>((_, reject) => {
          timer = setTimeout(
            () =>
              reject(new Error("Synthetic connection fixture did not start")),
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
const password = "Synthetic connection manager passphrase 73!";
const code = "c".repeat(43);
async function signIn(
  page: Page,
  installation: Installation,
  reviewer = false,
) {
  await page.goto(installation.url);
  await page
    .getByLabel("Email address", { exact: true })
    .fill(reviewer ? "reviewer@example.test" : "manager@example.test");
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page
    .getByLabel("Active pharmacy branch")
    .selectOption(installation.north);
}
async function openConnection(page: Page) {
  const menu = page.getByRole("button", {
    name: "Open navigation",
    exact: true,
  });
  if (await menu.isVisible()) await menu.click();
  await page
    .getByRole("button", { name: "Cloud connection", exact: true })
    .click();
  await expect(
    page.getByRole("button", {
      name: "Refresh connection status",
      exact: true,
    }),
  ).toBeEnabled();
  await expect(
    page.getByText("Camera monitoring: UNKNOWN", { exact: true }),
  ).toBeVisible();
}
async function prepare(page: Page) {
  await page
    .getByRole("button", { name: "Connect this branch", exact: true })
    .click();
  const form = page.getByRole("form", {
    name: "Prepare cloud pharmacy connection",
  });
  await form
    .getByLabel("Cloud HTTPS address", { exact: true })
    .fill("https://synthetic-control.example.test");
  await form.getByLabel("One-use connection code", { exact: true }).fill(code);
  await form
    .getByLabel("Laptop name", { exact: true })
    .fill("Synthetic laptop");
  await form
    .getByLabel("Your manager password", { exact: true })
    .fill(password);
  await form
    .getByRole("button", { name: "Check cloud identity", exact: true })
    .click();
}
async function change(page: Page, name: string) {
  await page.getByRole("button", { name, exact: true }).click();
  const form = page.getByRole("form", { name, exact: true });
  await form
    .getByLabel("Your manager password", { exact: true })
    .fill(password);
  await form.getByRole("button", { name, exact: true }).click();
}
async function noPersistedSecrets(page: Page) {
  const text = await page.evaluate(() =>
    JSON.stringify({
      url: location.href,
      local: { ...localStorage },
      session: { ...sessionStorage },
    }),
  );
  expect(text).not.toContain(password);
  expect(text).not.toContain(code);
  expect(text).not.toContain("t".repeat(64));
}

test("manager reviews the exact server identity, confirms PAUSED and explicitly resumes, pauses and disconnects", async ({
  page,
  installation,
}) => {
  const writes: string[] = [],
    responses: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().includes("/cloud-connection/"))
      writes.push(r.url().split("/").at(-1)!);
  });
  page.on("response", async (r) => {
    if (r.url().includes("/cloud-connection") && r.ok())
      responses.push(await r.text());
  });
  await signIn(page, installation);
  await openConnection(page);
  await prepare(page);
  const review = page.getByRole("form", {
    name: "Confirm cloud pharmacy connection",
  });
  await expect(
    review.getByLabel("Your manager password", { exact: true }),
  ).toHaveValue("");
  await expect(
    page.getByText(installation.identity.device_id, { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(installation.identity.organisation_id, { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(installation.identity.pharmacy_id, { exact: true }),
  ).toBeVisible();
  const confirm = review.getByRole("button", {
    name: "Confirm connection · keep sharing paused",
    exact: true,
  });
  await expect(confirm).toBeDisabled();
  await review.getByRole("checkbox").check();
  await review
    .getByLabel("Your manager password", { exact: true })
    .fill("Incorrect synthetic password");
  await confirm.click();
  await expect(page.getByRole("alert")).toContainText(
    "manager password was not accepted",
  );
  await expect(
    page.getByRole("button", { name: "Sign out", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Refresh connection status", exact: true })
    .click();
  await expect(review.getByRole("checkbox")).not.toBeChecked();
  await review.getByRole("checkbox").check();
  await review
    .getByLabel("Your manager password", { exact: true })
    .fill(password);
  await confirm.click();
  await expect(
    page.getByText("Metadata sharing: PAUSED", { exact: true }),
  ).toBeVisible();
  expect(writes).toEqual(["prepare", "confirm", "confirm"]);
  await change(page, "Resume metadata sharing");
  await expect(
    page.getByText("Metadata sharing: ACTIVE", { exact: true }),
  ).toBeVisible();
  await change(page, "Pause metadata sharing");
  await expect(
    page.getByText("Metadata sharing: PAUSED", { exact: true }),
  ).toBeVisible();
  await change(page, "Disconnect this branch");
  await expect(
    page.getByText("Metadata sharing: DISCONNECTED", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(/Private connection credentials are unavailable/),
  ).toHaveCount(0);
  expect(writes).toEqual([
    "prepare",
    "confirm",
    "confirm",
    "resume",
    "pause",
    "disconnect",
  ]);
  const status = await (
    await page.request.get(`${installation.url}/api/cloud-connection`)
  ).json();
  expect(status.connection.state).toBe("DISCONNECTED");
  // Closed bindings deliberately do not load private credentials; this is not
  // a storage failure and must not produce a missing-key warning in the UI.
  expect(status.connection.credential_available).toBe(false);
  await page
    .getByRole("button", { name: "Refresh connection status", exact: true })
    .click();
  await expect(
    page.getByText("Metadata sharing: DISCONNECTED", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(/Private connection credentials are unavailable/),
  ).toHaveCount(0);
  expect(status.monitoring_status).toBe("UNKNOWN");
  for (const response of responses)
    for (const secret of [password, code, "t".repeat(64)])
      expect(response).not.toContain(secret);
  await noPersistedSecrets(page);
});

test("navigation and branch switching discard a late preparation without confirming or activating it", async ({
  page,
  installation,
}) => {
  await signIn(page, installation);
  await openConnection(page);
  let release!: () => void, prepared!: () => void;
  const gate = new Promise<void>((done) => {
    release = done;
  });
  const reached = new Promise<void>((done) => {
    prepared = done;
  });
  const writes: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().includes("/cloud-connection/"))
      writes.push(r.url());
  });
  await page.route("**/api/cloud-connection/prepare", async (route) => {
    const response = await route.fetch();
    prepared();
    await gate;
    await route.fulfill({ response }).catch(() => {});
  });
  await prepare(page);
  await reached;
  await expect(
    page.locator('.cloud-connection input[type="password"]'),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Overview", exact: true }).click();
  release();
  await page
    .getByLabel("Active pharmacy branch")
    .selectOption(installation.south);
  await openConnection(page);
  await expect(
    page.getByText("No cloud connection", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("form", { name: "Confirm cloud pharmacy connection" }),
  ).toHaveCount(0);
  await expect(
    page.getByText(installation.identity.device_id, { exact: true }),
  ).toHaveCount(0);
  expect(writes).toHaveLength(1);
  await noPersistedSecrets(page);
});

test("uncertain registration requires status inspection, without automatic enrolment retry", async ({
  page,
  installation,
}) => {
  await writeFile(
    join(installation.directory, "fail-identity"),
    "synthetic failure",
  );
  await signIn(page, installation);
  await openConnection(page);
  let attempts = 0;
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().endsWith("/cloud-connection/prepare"))
      attempts++;
  });
  await prepare(page);
  await expect(page.getByRole("alert")).toContainText(
    "Registration may have succeeded",
  );
  await page
    .getByRole("button", { name: "Refresh connection status", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText(
    "Registration outcome is uncertain",
  );
  await expect(
    page.getByRole("form", { name: "Confirm cloud pharmacy connection" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Resume metadata sharing", exact: true }),
  ).toHaveCount(0);
  expect(attempts).toBe(1);
  await noPersistedSecrets(page);
});

test("expired reviews clear approval inputs and cannot confirm; the form fits mobile and keyboard access", async ({
  page,
  installation,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, installation);
  await openConnection(page);
  await prepare(page);
  const form = page.getByRole("form", {
    name: "Confirm cloud pharmacy connection",
  });
  await form.getByRole("checkbox").check();
  await form
    .getByLabel("Your manager password", { exact: true })
    .fill(password);
  await form.getByLabel("Your manager password", { exact: true }).press("Tab");
  await expect(
    form.getByRole("button", {
      name: "Confirm connection · keep sharing paused",
      exact: true,
    }),
  ).toBeFocused();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
  expect(
    (await new AxeBuilder({ page }).include(".cloud-connection").analyze())
      .violations,
  ).toEqual([]);
  await mkdir(resolve(".local/release-evidence"), { recursive: true });
  await page.screenshot({
    path: resolve(".local/release-evidence/cloud-connection-mobile.png"),
    fullPage: true,
  });
  let confirms = 0;
  page.on("request", (r) => {
    if (r.url().endsWith("/cloud-connection/confirm")) confirms++;
  });
  await page.clock.install();
  await page.clock.setSystemTime(new Date(Date.now() + 301000));
  await expect(page.getByText(/This review has expired/)).toBeVisible();
  await expect(form).toHaveCount(0);
  expect(confirms).toBe(0);
  await noPersistedSecrets(page);
});

test("reviewers have no Cloud connection tab and the real endpoint rejects their role", async ({
  page,
  installation,
}) => {
  await signIn(page, installation, true);
  await expect(
    page.getByRole("button", { name: "Cloud connection", exact: true }),
  ).toHaveCount(0);
  expect(
    (
      await page.request.get(`${installation.url}/api/cloud-connection`)
    ).status(),
  ).toBe(403);
});

test("a concurrent connection change requires fresh status and manager intent", async ({
  page,
  installation,
}) => {
  await signIn(page, installation);
  await openConnection(page);
  await prepare(page);
  const review = page.getByRole("form", {
    name: "Confirm cloud pharmacy connection",
  });
  await review.getByRole("checkbox").check();
  await review
    .getByLabel("Your manager password", { exact: true })
    .fill(password);
  await review
    .getByRole("button", {
      name: "Confirm connection · keep sharing paused",
      exact: true,
    })
    .click();
  await expect(
    page.getByText("Metadata sharing: PAUSED", { exact: true }),
  ).toBeVisible();
  const session = await (
    await page.request.get(`${installation.url}/api/session`)
  ).json();
  const before = await (
    await page.request.get(`${installation.url}/api/cloud-connection`)
  ).json();
  const response = await page.request.post(
    `${installation.url}/api/cloud-connection/resume`,
    {
      headers: {
        Origin: installation.url,
        "X-CSRF-Token": session.csrf_token,
        "X-AisleSignals-Site": installation.north,
        "Idempotency-Key": crypto.randomUUID(),
      },
      data: {
        binding_id: before.connection.binding_id,
        expected_generation: before.connection.generation,
        manager_password: password,
      },
    },
  );
  expect(response.status()).toBe(200);
  await change(page, "Disconnect this branch");
  await expect(page.getByRole("alert")).toContainText("connection changed");
  await expect(
    page.getByText("Status needs checking", { exact: true }),
  ).toBeVisible();
  const current = await (
    await page.request.get(`${installation.url}/api/cloud-connection`)
  ).json();
  expect(current.connection.state).toBe("ACTIVE");
  await page
    .getByRole("button", { name: "Refresh connection status", exact: true })
    .click();
  await expect(
    page.getByText("Metadata sharing: ACTIVE", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Disconnect this branch", exact: true })
    .click();
  await expect(
    page.getByLabel("Your manager password", { exact: true }),
  ).toHaveValue("");
  await noPersistedSecrets(page);
});

test("offline and sign-out clear unsent credentials without registering a laptop", async ({
  page,
  context,
  installation,
}) => {
  await signIn(page, installation);
  await openConnection(page);
  let writes = 0;
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().includes("/cloud-connection/"))
      writes++;
  });
  await page
    .getByRole("button", { name: "Connect this branch", exact: true })
    .click();
  await page.getByLabel("One-use connection code", { exact: true }).fill(code);
  await page
    .getByLabel("Your manager password", { exact: true })
    .fill(password);
  await context.setOffline(true);
  await expect(
    page.getByRole("form", { name: "Prepare cloud pharmacy connection" }),
  ).toHaveCount(0);
  await expect(
    page.getByText("Status needs checking", { exact: true }),
  ).toBeVisible();
  await context.setOffline(false);
  await page
    .getByRole("button", { name: "Refresh connection status", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Connect this branch", exact: true })
    .click();
  await expect(
    page.getByLabel("One-use connection code", { exact: true }),
  ).toHaveValue("");
  await expect(
    page.getByLabel("Your manager password", { exact: true }),
  ).toHaveValue("");
  await page.getByLabel("One-use connection code", { exact: true }).fill(code);
  await page
    .getByLabel("Your manager password", { exact: true })
    .fill(password);
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Sign in", exact: true }),
  ).toBeVisible();
  await signIn(page, installation);
  await openConnection(page);
  await expect(
    page.getByText("No cloud connection", { exact: true }),
  ).toBeVisible();
  expect(writes).toBe(0);
  await noPersistedSecrets(page);
});
