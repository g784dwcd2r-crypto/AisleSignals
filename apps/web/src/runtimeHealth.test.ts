import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  RuntimeHealthMonitor,
  watchRuntimeHealth,
  type RuntimeHealthReport,
} from "./runtimeHealth";
const report = (
  change: Partial<RuntimeHealthReport> = {},
): RuntimeHealthReport => ({
  api_id: "a".repeat(32),
  runtime_id: "b".repeat(32),
  recovery_generation: 0,
  context: "c".repeat(64),
  site_id: "site-a",
  supervised: true,
  state: "SERVICES_READY",
  monitoring_allowed: true,
  product_available: true,
  report_age_ms: 100,
  ...change,
});
let monitor: RuntimeHealthMonitor;
beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("window", new EventTarget());
  vi.stubGlobal(
    "document",
    Object.assign(new EventTarget(), { hidden: false }),
  );
  vi.stubGlobal("navigator", { onLine: true });
  monitor = new RuntimeHealthMonitor();
  monitor.begin("site-a");
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
function accept(change: Partial<RuntimeHealthReport> = {}) {
  monitor.accept(report(change), monitor.requestEpoch());
}

describe("runtime context and explicit recovery", () => {
  it("blocks monitoring until a scoped fresh heartbeat, never infers an armed alarm", () => {
    expect(monitor.canMonitor()).toBe(false);
    accept();
    expect(monitor.canMonitor()).toBe(true);
    expect(monitor.snapshot()).not.toHaveProperty("alarmEnabled");
  });
  it.each([
    { monitoring_allowed: false },
    { state: "DEGRADED" },
    { state: "RECOVERING" },
    { report_age_ms: 8001 },
    { report_age_ms: -2001 },
    { site_id: "other" },
    { recovery_generation: -1 },
    { context: "" },
    { api_id: "invalid" },
  ])("fails closed for invalid/degraded health %j", (change) => {
    accept();
    const stopped = vi.fn();
    monitor.onInterrupt(stopped);
    accept(change);
    expect(stopped).toHaveBeenCalledOnce();
    expect(monitor.snapshot()).toMatchObject({
      ready: false,
      context: null,
      interrupted: true,
    });
  });
  it("invalidates synchronously before publishing a fresh generation, keeping restart required", () => {
    accept();
    const states: boolean[] = [];
    monitor.onInterrupt(() => states.push(monitor.snapshot().ready));
    accept({
      recovery_generation: 1,
      context: "d".repeat(64),
      state: "REARM_REQUIRED",
    });
    expect(states).toEqual([false]);
    expect(monitor.snapshot()).toMatchObject({
      ready: true,
      interrupted: true,
    });
    expect(monitor.acknowledgeStart()).toBe(true);
    expect(monitor.snapshot().interrupted).toBe(false);
  });
  it("does not let a pending pre-outage or prior-branch heartbeat undo interruption", () => {
    accept();
    const old = monitor.requestEpoch();
    monitor.interrupt("offline");
    monitor.accept(report(), old);
    expect(monitor.snapshot().ready).toBe(false);
    monitor.begin("site-b");
    monitor.accept(report(), old);
    expect(monitor.snapshot().ready).toBe(false);
    monitor.accept(report({ site_id: "site-b" }), monitor.requestEpoch());
    expect(monitor.canMonitor()).toBe(true);
  });
  it("stops on heartbeat expiry and wall-clock sleep even before queued results run", () => {
    accept();
    monitor.tick(Date.now() + 5000, performance.now() + 5000);
    expect(monitor.snapshot().ready).toBe(false);
    accept();
    monitor.tick(Date.now() + 50000, performance.now() + 10);
    expect(monitor.snapshot().ready).toBe(false);
  });
  it("keeps API-only mode explicitly unsupervised and detects an API process replacement", () => {
    accept({
      supervised: false,
      runtime_id: null,
      report_age_ms: null,
      state: "API_ONLY",
    });
    expect(monitor.snapshot().message).toContain("not supervised");
    accept({
      supervised: false,
      runtime_id: null,
      report_age_ms: null,
      state: "API_ONLY",
      api_id: "d".repeat(32),
    });
    expect(monitor.snapshot().interrupted).toBe(true);
  });
});

describe("bounded heartbeat lifecycle", () => {
  it("bounds hung response/body, aborts, and recovers without clearing the restart latch", async () => {
    accept();
    let signal: AbortSignal;
    const read = vi
      .fn()
      .mockImplementationOnce((value) => {
        signal = value;
        return new Promise(() => {});
      })
      .mockResolvedValue(report());
    const close = watchRuntimeHealth(monitor, read);
    await vi.advanceTimersByTimeAsync(1001);
    expect(signal!.aborted).toBe(true);
    expect(monitor.snapshot()).toMatchObject({
      ready: false,
      interrupted: true,
    });
    expect(read).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(2000);
    expect(read).toHaveBeenCalledTimes(2);
    expect(monitor.snapshot()).toMatchObject({
      ready: true,
      interrupted: true,
    });
    close();
    await vi.advanceTimersByTimeAsync(5000);
    expect(read).toHaveBeenCalledTimes(2);
  });
  it("offline event stops before awaiting any network result; old result is discarded", async () => {
    accept();
    let resolve!: (value: RuntimeHealthReport) => void;
    const close = watchRuntimeHealth(
      monitor,
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    window.dispatchEvent(new Event("offline"));
    expect(monitor.snapshot().ready).toBe(false);
    resolve(report());
    await Promise.resolve();
    await Promise.resolve();
    expect(monitor.snapshot().ready).toBe(false);
    close();
  });
  it("hidden page and disposed watcher cannot certify or resume monitoring", async () => {
    accept();
    let resolve!: (value: RuntimeHealthReport) => void;
    const close = watchRuntimeHealth(
      monitor,
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    Object.assign(document, { hidden: true });
    document.dispatchEvent(new Event("visibilitychange"));
    expect(monitor.snapshot().ready).toBe(false);
    close();
    resolve(report());
    await Promise.resolve();
    await Promise.resolve();
    expect(monitor.snapshot().ready).toBe(false);
  });
});

import { api, clearSession, setCsrf, ApiError } from "./api";
import { runtimeHealth } from "./runtimeHealth";
describe("ordinary API requests share the runtime fence", () => {
  beforeEach(() => {
    clearSession();
    setCsrf("synthetic-csrf");
    runtimeHealth.begin("site-a");
    runtimeHealth.accept(report(), runtimeHealth.requestEpoch());
  });
  afterEach(() => {
    runtimeHealth.end();
    clearSession();
  });
  it("rejects a late successful live result after interruption even after healthy recovery", async () => {
    let done!: (response: Response) => void;
    const fetcher = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          done = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetcher);
    const result = api("/interactions/jobs/synthetic-id");
    runtimeHealth.interrupt("connection lost");
    runtimeHealth.accept(report(), runtimeHealth.requestEpoch());
    done(new Response(JSON.stringify({ status: "completed" })));
    await expect(result).rejects.toMatchObject({
      code: "RUNTIME_CONTEXT_CHANGED",
    });
    expect(fetcher).toHaveBeenCalledWith(
      "/api/interactions/jobs/synthetic-id",
      expect.objectContaining({
        headers: expect.objectContaining({
          "X-AisleSignals-Runtime": "c".repeat(64),
        }),
      }),
    );
  });
  it("stops before exposing an ordinary failed request and blocks further monitoring requests", async () => {
    const stop = vi.fn();
    const release = runtimeHealth.onInterrupt(stop);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("synthetic offline")),
    );
    await expect(api("/bootstrap")).rejects.toBeInstanceOf(ApiError);
    expect(stop).toHaveBeenCalledOnce();
    await expect(api("/interactions/jobs", "POST", {})).rejects.toMatchObject({
      code: "RUNTIME_CONTEXT_CHANGED",
    });
    expect(fetch).toHaveBeenCalledTimes(1);
    release();
  });
  it("does not let a failed request from an ended branch disable a new healthy context", async () => {
    let fail!: (failure: Error) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((_, reject) => {
            fail = reject;
          }),
      ),
    );
    const old = api("/bootstrap");
    runtimeHealth.begin("site-b");
    runtimeHealth.accept(
      report({ site_id: "site-b" }),
      runtimeHealth.requestEpoch(),
    );
    fail(new Error("old branch request failed"));
    await expect(old).rejects.toBeInstanceOf(ApiError);
    expect(runtimeHealth.canMonitor()).toBe(true);
  });
});
