import { test as base, expect, type Page } from "@playwright/test";
import { spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

// Each browser journey owns a fresh real pilot API/SQLite/evidence directory.
// Only the vision provider is mocked; no customer preview or model is contacted.
const server = String.raw`
import json, os, socket, sys, threading, time, asyncio
from datetime import datetime, timezone
from pathlib import Path
for key in list(os.environ):
    if key.startswith('AISLESIGNALS_'): del os.environ[key]
listener=socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen(128)
port=listener.getsockname()[1]
unavailable=socket.socket(); unavailable.bind(('127.0.0.1',0))
os.environ['AISLESIGNALS_MODE']='pilot'
os.environ['AISLESIGNALS_PILOT_SUPERVISED_CHILD']='1'
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
app.state.interactions.provider=MockProvider()
control_path=Path(sys.argv[1]).resolve()/'control.json'
control_path.write_text('{}')
def control():
    try: return json.loads(control_path.read_text())
    except (ValueError, OSError): return {}
def report_loop():
    while True:
        state=control()
        if state.get('hold_model'): app.state.interactions.provider.release.clear()
        else: app.state.interactions.provider.release.set()
        stamp=datetime.now(timezone.utc).isoformat()
        report=dict(schema_version=1, runtime_id='a'*32, recovery_generation=state.get('generation',0), generated_at=stamp,
            state='DEGRADED' if state.get('suspect') else 'SERVICES_READY', services=[
                dict(name=name,health='READY',running=True,disabled=False,last_checked_at=stamp) for name in ('api','vision')])
        target=control_path.with_name('runtime-status.json')
        staging=target.with_suffix('.tmp'); staging.write_text(json.dumps(report)); staging.replace(target)
        time.sleep(.1)
threading.Thread(target=report_loop,daemon=True).start()
@app.middleware('http')
async def synthetic_fault(request, call_next):
    if request.url.path=='/api/runtime/health':
        while control().get('hang'): await asyncio.sleep(.05)
    return await call_next(request)
import uvicorn
print('CASE_READY '+json.dumps({'url':'http://127.0.0.1:'+str(port),'north':initial['site']['id'],'south':other['id']}),flush=True)
uvicorn.Server(uvicorn.Config(app,log_level='warning',access_log=False,proxy_headers=False)).run(sockets=[listener])
`;
type Installation = {
  url: string;
  north: string;
  south: string;
  control: (state: Record<string, unknown>) => Promise<void>;
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
      await use({
        ...installation,
        control: (state) =>
          writeFile(join(directory, "control.json"), JSON.stringify(state)),
      });
    } finally {
      if (child.exitCode === null) child.kill("SIGTERM");
      await closed;
      await rm(directory, { recursive: true, force: true });
    }
  },
});

async function open(page: Page, installation: Installation) {
  await page.addInitScript(() => {
    const probe = ((window as any).__runtime = {
      calls: 0,
      terminations: 0,
      tones: 0,
      disconnected: 0,
      hold: false,
      results: [] as (() => void)[],
      tracks: [] as MediaStreamTrack[],
    });
    class WorkerFixture {
      onmessage: ((event: any) => void) | null = null;
      postMessage(request: any) {
        request.bitmap?.close();
        const done = () =>
          this.onmessage?.({
            data:
              request.type === "init"
                ? { type: "ready" }
                : { type: "result", id: request.id, poses: [] },
          });
        if (request.type === "frame") {
          probe.calls++;
          if (probe.hold) {
            probe.results.push(done);
            return;
          }
        }
        queueMicrotask(done);
      }
      terminate() {
        probe.terminations++;
      }
    }
    (window as any).Worker = WorkerFixture;
    // Entire audio boundary is synthetic: no real oscillator or sound is created.
    const parameter = () => ({
      setValueAtTime() {},
      linearRampToValueAtTime() {},
      cancelScheduledValues() {},
    });
    class SilentContext {
      state = "running";
      currentTime = 0;
      destination = {};
      onstatechange = null;
      async resume() {}
      async close() {
        this.state = "closed";
      }
      createGain() {
        return {
          gain: parameter(),
          connect() {},
          disconnect() {
            probe.disconnected++;
          },
        };
      }
      createOscillator() {
        return {
          frequency: parameter(),
          connect() {},
          disconnect() {},
          start() {
            probe.tones++;
          },
          stop() {},
          onended: null,
          type: "sine",
        };
      }
    }
    (window as any).AudioContext = SilentContext;
    navigator.mediaDevices.getUserMedia = async () => {
      const canvas = document.createElement("canvas");
      canvas.width = 480;
      canvas.height = 270;
      const context = canvas.getContext("2d")!;
      let frame = 0;
      const draw = () => {
        context.fillStyle = `rgb(${30 + (frame++ % 170)},70,110)`;
        context.fillRect(0, 0, 480, 270);
        context.fillStyle = "#fff";
        context.font = "24px sans-serif";
        context.fillText("SYNTHETIC RUNTIME TEST", 20, 120);
      };
      draw();
      const stream = canvas.captureStream(10);
      probe.tracks = stream.getTracks();
      const timer = setInterval(() => {
        if (stream.getVideoTracks()[0].readyState === "ended")
          clearInterval(timer);
        else draw();
      }, 100);
      return stream;
    };
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
}
async function start(page: Page) {
  await page
    .getByRole("button", { name: "Connect camera", exact: true })
    .click();
  await page
    .getByRole("combobox", { name: "Camera layout", exact: true })
    .selectOption("single");
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
}
async function stopped(page: Page) {
  await expect(
    page.getByText("POSE TRACKING RUNNING", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByLabel("Enable product interaction analysis", { exact: true }),
  ).not.toBeChecked();
  await expect(
    page.getByLabel("Analyse automatically", { exact: true }),
  ).not.toBeChecked();
  await expect(
    page.getByLabel("Sound on movement-rule alerts"),
  ).not.toBeChecked();
  await expect
    .poll(() =>
      page.evaluate(() =>
        (window as any).__runtime.tracks.every(
          (track: MediaStreamTrack) => track.readyState === "ended",
        ),
      ),
    )
    .toBe(true);
  await expect(
    page.getByText("0/4 fresh sampled frames", { exact: true }),
  ).toBeVisible();
}

test("a hung real API heartbeat stops pose, clears samples and cancels a pending product job; recovery stays stopped", async ({
  page,
  installation,
}) => {
  await installation.control({ hold_model: true });
  await open(page, installation);
  await start(page);
  const submitted = await page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/interactions/jobs") &&
      response.request().method() === "POST",
  );
  expect(submitted.status()).toBe(202);
  const id = (await submitted.json()).id;
  for (const label of [
    "Entrance zone is visible",
    "Exit zone is visible",
    "Cashier zone is visible",
    "Relevant shelf zones are visible",
  ])
    await page.getByLabel(label, { exact: true }).check();
  await page
    .getByRole("button", { name: "Test product alarm sound", exact: true })
    .click();
  await page
    .getByRole("button", { name: "I heard the test tone", exact: true })
    .click();
  await page
    .getByLabel("Experimental product attention alarm", { exact: true })
    .check();
  expect(await page.evaluate(() => (window as any).__runtime.tones)).toBe(1);
  await installation.control({ hang: true, hold_model: true });
  await stopped(page);
  await expect(
    page.getByLabel("Experimental product attention alarm", { exact: true }),
  ).not.toBeChecked();
  await expect(page.getByLabel("Runtime monitoring health")).toContainText(
    "not responding",
  );
  await installation.control({});
  await expect(page.getByLabel("Runtime monitoring health")).toContainText(
    "recovered",
  );
  await expect
    .poll(async () => {
      const health = await (
        await page.request.get(`${installation.url}/api/runtime/health`)
      ).json();
      return (
        await (
          await page.request.get(
            `${installation.url}/api/interactions/jobs/${id}`,
            { headers: { "X-AisleSignals-Runtime": health.context } },
          )
        ).json()
      ).status;
    })
    .toBe("cancelled");
  const calls = await page.evaluate(() => (window as any).__runtime.calls);
  await page.waitForTimeout(1200);
  expect(await page.evaluate(() => (window as any).__runtime.calls)).toBe(
    calls,
  );
  expect(await page.evaluate(() => (window as any).__runtime.tones)).toBe(1);
  await expect(
    page.getByRole("button", { name: "Start detection", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Casebook", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Casebook", exact: true }),
  ).toBeVisible();
});

test("healthy new generation stops monitoring and requires a fresh explicit camera/start/product choice", async ({
  page,
  installation,
}) => {
  await open(page, installation);
  await start(page);
  await installation.control({ generation: 1 });
  await stopped(page);
  await expect(page.getByLabel("Runtime monitoring health")).toContainText(
    "recovered",
  );
  await start(page);
  await expect(
    page.getByText("POSE TRACKING RUNNING", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByLabel("Experimental product attention alarm", { exact: true }),
  ).not.toBeChecked();
});

test("browser offline immediately tears down source and ignores a late pose callback", async ({
  page,
  installation,
}) => {
  await open(page, installation);
  await start(page);
  await page.evaluate(() => {
    (window as any).__runtime.hold = true;
  });
  await expect
    .poll(() => page.evaluate(() => (window as any).__runtime.results.length))
    .toBeGreaterThan(0);
  await page.context().setOffline(true);
  await stopped(page);
  await page.evaluate(() => {
    (window as any).__runtime.results
      .splice(0)
      .forEach((done: () => void) => done());
  });
  await page.context().setOffline(false);
  await expect(page.getByLabel("Runtime monitoring health")).toContainText(
    "recovered",
  );
  await expect(
    page.getByText("POSE TRACKING RUNNING", { exact: true }),
  ).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__runtime.tones)).toBe(0);
});

test("clock discontinuity invalidates current frames before any resumed result or sound", async ({
  page,
  installation,
}) => {
  await open(page, installation);
  await start(page);
  await page.evaluate(() => {
    const original = Date.now;
    Date.now = () => original() + 60000;
  });
  await stopped(page);
  await expect(
    page.getByText("POSE TRACKING RUNNING", { exact: true }),
  ).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__runtime.tones)).toBe(0);
});
