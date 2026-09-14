import type { Device } from "./types";

/** Cached ONLINE never certifies current presence after its heartbeat expires. */
export function connectionStatus(
  device: Pick<Device, "connection_status" | "last_seen_at">,
  now = Date.now(),
): Device["connection_status"] | "STALE_REPORT" {
  if (device.connection_status !== "ONLINE") return device.connection_status;
  const seen = device.last_seen_at
    ? new Date(device.last_seen_at).getTime()
    : NaN;
  const age = now - seen;
  return Number.isFinite(age) && age >= -30_000 && age <= 120_000
    ? "ONLINE"
    : "STALE_REPORT";
}
