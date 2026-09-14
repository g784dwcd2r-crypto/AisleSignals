import { describe, expect, it, vi } from "vitest";
import { IDLE_LIMIT_MS, IdleSessionGuard } from "./idleSession";

describe("local inactivity lock", () => {
  it("background checks never postpone the15-minute boundary", () => {
    let now = 0;
    const locked = vi.fn();
    const guard = new IdleSessionGuard(locked, () => now);
    for (now = 30_000; now < IDLE_LIMIT_MS; now += 30_000)
      expect(guard.check()).toBe(false);
    expect(guard.check()).toBe(true);
    guard.check();
    expect(locked).toHaveBeenCalledOnce();
  });
  it("real activity extends an unexpired session", () => {
    let now = 0;
    const locked = vi.fn();
    const guard = new IdleSessionGuard(locked, () => now);
    now = IDLE_LIMIT_MS - 1;
    guard.activity();
    now += IDLE_LIMIT_MS - 1;
    expect(guard.check()).toBe(false);
    now += 1;
    expect(guard.check()).toBe(true);
  });
  it("a first input after a suspended/hidden interval cannot revive an expired session", () => {
    let now = 1000;
    const locked = vi.fn();
    const guard = new IdleSessionGuard(locked, () => now);
    now += IDLE_LIMIT_MS + 5000;
    guard.activity();
    expect(locked).toHaveBeenCalledOnce();
    expect(guard.check()).toBe(true);
  });
  it("a clock rollback locks instead of extending access indefinitely", () => {
    let now = 10_000;
    const locked = vi.fn();
    const guard = new IdleSessionGuard(locked, () => now);
    now = 9000;
    guard.activity();
    expect(locked).toHaveBeenCalledOnce();
  });
});
