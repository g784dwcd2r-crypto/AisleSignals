/** Process/report identity is a freshness fence, never an authentication token. */
export type RuntimeHealthReport = {
  api_id: string;
  runtime_id: string | null;
  recovery_generation: number;
  context: string | null;
  site_id: string;
  supervised: boolean;
  state: string;
  monitoring_allowed: boolean;
  product_available: boolean | null;
  report_age_ms: number | null;
};
export type RuntimeHealthState = {
  active: boolean;
  ready: boolean;
  interrupted: boolean;
  revision: number;
  context: string | null;
  message: string;
};
const initial = (): RuntimeHealthState => ({
  active: false,
  ready: false,
  interrupted: false,
  revision: 0,
  context: null,
  message: "Checking local service health before monitoring.",
});
export const HEALTH_DEADLINE_MS = 1000;
export const HEALTH_INTERVAL_MS = 2000;
export const HEALTH_FRESH_MS = 4000;

export class RuntimeHealthMonitor {
  private value = initial();
  private listeners = new Set<() => void>();
  private interrupters = new Set<(reason: string) => void>();
  private epoch = 0;
  private site = "";
  private lastHealthy = -Infinity;
  private lastWall = 0;
  private lastMono = 0;
  private identity: string | null = null;
  snapshot = () => this.value;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  onInterrupt = (listener: (reason: string) => void) => {
    this.interrupters.add(listener);
    return () => {
      this.interrupters.delete(listener);
    };
  };
  private publish(value: RuntimeHealthState) {
    this.value = value;
    this.listeners.forEach((listener) => listener());
  }
  begin(site: string, wall = Date.now(), mono = performance.now()) {
    this.end();
    this.site = site;
    this.lastWall = wall;
    this.lastMono = mono;
    this.publish({ ...initial(), active: true, revision: this.value.revision });
    return this.epoch;
  }
  end() {
    this.epoch++;
    if (this.value.active)
      this.interrupt("Session changed. Monitoring stopped.");
    this.site = "";
    this.identity = null;
    this.lastHealthy = -Infinity;
    this.publish({ ...initial(), revision: this.value.revision });
  }
  requestEpoch() {
    return this.epoch;
  }
  isCurrent(epoch: number) {
    return epoch === this.epoch && this.value.active;
  }
  interrupt(reason: string, epoch = this.epoch) {
    if (!this.isCurrent(epoch)) return;
    // Ref-based consumers stop before any React render or late request continuation.
    const changed = this.value.ready || !this.value.interrupted;
    this.value = {
      ...this.value,
      ready: false,
      interrupted: true,
      context: null,
      revision: this.value.revision + (changed ? 1 : 0),
      message: reason,
    };
    if (changed) this.epoch++;
    if (changed) this.interrupters.forEach((listener) => listener(reason));
    this.listeners.forEach((listener) => listener());
  }
  tick(wall = Date.now(), mono = performance.now()) {
    if (!this.value.active) return;
    const elapsed = mono - this.lastMono;
    const drift = Math.abs(wall - this.lastWall - elapsed);
    this.lastWall = wall;
    this.lastMono = mono;
    if (elapsed < 0 || elapsed > HEALTH_FRESH_MS || drift > 1500) {
      this.epoch++; // A response sent before sleep cannot certify the resumed context.
      this.interrupt(
        "Laptop sleep or clock interruption detected. Monitoring stopped; wait for fresh health, then start explicitly.",
      );
      this.lastHealthy = -Infinity;
    } else if (this.value.ready && mono - this.lastHealthy > HEALTH_FRESH_MS) {
      this.interrupt(
        "Local service heartbeat expired. Monitoring stopped and sound disarmed.",
      );
    }
  }
  accept(report: RuntimeHealthReport, epoch: number, mono = performance.now()) {
    if (!this.isCurrent(epoch)) return;
    if (
      !report ||
      typeof report.supervised !== "boolean" ||
      report.site_id !== this.site ||
      !/^[a-f0-9]{32}$/.test(report.api_id) ||
      !Number.isSafeInteger(report.recovery_generation) ||
      report.recovery_generation < 0 ||
      !/^[a-f0-9]{64}$/.test(report.context ?? "") ||
      report.monitoring_allowed !== true ||
      (report.supervised &&
        (typeof report.report_age_ms !== "number" ||
          !Number.isFinite(report.report_age_ms) ||
          report.report_age_ms < -2000 ||
          report.report_age_ms > 8000 ||
          !/^[a-f0-9]{32}$/.test(report.runtime_id ?? "") ||
          (!["SERVICES_READY", "REARM_REQUIRED"].includes(report.state) &&
            !(
              report.state === "DEGRADED" && report.product_available === false
            ))))
    ) {
      this.interrupt(
        "Local runtime is unavailable or recovering. Monitoring stopped and sound disarmed.",
        epoch,
      );
      return;
    }
    const identity = `${report.api_id}:${report.runtime_id}:${report.recovery_generation}:${report.context}`;
    if (this.identity !== null && identity !== this.identity)
      this.interrupt(
        "Local service restarted. Previous monitoring and sound settings were cleared; start explicitly.",
        epoch,
      );
    this.identity = identity;
    this.lastHealthy = mono;
    this.publish({
      ...this.value,
      ready: true,
      context: report.context,
      message: this.value.interrupted
        ? "Service connection recovered. Monitoring remains stopped. Start detection again, enable product analysis, and repeat the sound check before arming."
        : report.supervised
          ? report.product_available === false
            ? "Local API responding. Product model is disabled; only casework and local pose tracking are available."
            : "Local services responding. Camera coverage and physical sound still require operator checks."
          : "Local API responding. Launcher and model process health are not supervised in this mode.",
    });
  }
  canMonitor(mono = performance.now()) {
    this.tick(Date.now(), mono);
    return (
      this.value.active &&
      this.value.ready &&
      mono - this.lastHealthy <= HEALTH_FRESH_MS
    );
  }
  acknowledgeStart() {
    if (!this.canMonitor()) return false;
    this.publish({
      ...this.value,
      interrupted: false,
      message:
        "Local service connection checked. Source and alarm checks remain separate.",
    });
    return true;
  }
}
export const runtimeHealth = new RuntimeHealthMonitor();

/** One request at a time; deadline includes body decoding, even for a hung fetch. */
export function watchRuntimeHealth(
  monitor: RuntimeHealthMonitor,
  read: (signal: AbortSignal) => Promise<RuntimeHealthReport>,
) {
  let closed = false;
  let controller: AbortController | null = null;
  let next: ReturnType<typeof setTimeout> | undefined;
  let deadline: ReturnType<typeof setTimeout> | undefined;
  const clock = setInterval(() => monitor.tick(), 250);
  const offline = () =>
    monitor.interrupt(
      "Browser is offline. Monitoring stopped and sound disarmed.",
    );
  const hidden = () => {
    if (document.hidden)
      monitor.interrupt(
        "Monitoring stopped because this page was hidden. Return and start explicitly.",
      );
  };
  window.addEventListener("offline", offline);
  document.addEventListener("visibilitychange", hidden);
  async function probe() {
    const epoch = monitor.requestEpoch();
    controller = new AbortController();
    try {
      const report = await Promise.race([
        read(controller.signal),
        new Promise<never>((_, reject) => {
          deadline = setTimeout(() => {
            controller?.abort();
            reject(new Error("Heartbeat deadline exceeded"));
          }, HEALTH_DEADLINE_MS);
        }),
      ]);
      monitor.tick();
      if (!closed && !document.hidden && navigator.onLine !== false)
        monitor.accept(report, epoch);
    } catch {
      if (!closed)
        monitor.interrupt(
          "Local service is not responding. Monitoring stopped and sound disarmed. Reopen the launcher if it does not recover.",
          epoch,
        );
    } finally {
      clearTimeout(deadline);
      if (!closed) next = setTimeout(() => void probe(), HEALTH_INTERVAL_MS);
    }
  }
  void probe();
  return () => {
    closed = true;
    controller?.abort();
    clearTimeout(deadline);
    clearTimeout(next);
    clearInterval(clock);
    window.removeEventListener("offline", offline);
    document.removeEventListener("visibilitychange", hidden);
  };
}
