import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  api,
  ApiError,
  clearSession,
  idempotencyKey,
  isViewLocked,
  lockView,
  setCsrf,
} from "./api";

beforeEach(() => clearSession());
afterEach(() => vi.unstubAllGlobals());

describe("uncertain write recovery", () => {
  it("keeps the same idempotency key after a truncated successful response", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        new Response('{"id":', {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response('{"id":"created-once"}', { status: 200 }),
      );
    vi.stubGlobal("fetch", fetcher);
    const payload = { title: "Synthetic case", notes: "Synthetic test notes." };
    const key = idempotencyKey("/incidents", "POST", payload);
    await expect(api("/incidents", "POST", payload)).rejects.toMatchObject({
      code: "INCOMPLETE_RESPONSE",
      status: 0,
    });
    expect(idempotencyKey("/incidents", "POST", payload)).toBe(key);
    expect(await api("/incidents", "POST", payload)).toEqual({
      id: "created-once",
    });
    const first = fetcher.mock.calls[0][1].headers["Idempotency-Key"];
    const second = fetcher.mock.calls[1][1].headers["Idempotency-Key"];
    expect(first).toBe(second);
    expect(idempotencyKey("/incidents", "POST", payload)).not.toBe(key);
  });
  it("keeps retry identity when the request fails before a response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("network offline")),
    );
    const payload = { reason: "Synthetic assistance" };
    const key = idempotencyKey("/assistance", "POST", payload);
    await expect(api("/assistance", "POST", payload)).rejects.toMatchObject({
      status: 0,
    });
    expect(idempotencyKey("/assistance", "POST", payload)).toBe(key);
    expect(
      idempotencyKey("/assistance", "POST", {
        reason: "Changed logical request",
      }),
    ).not.toBe(key);
  });
  it("surfaces version conflicts without falsely acknowledging success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: {
              code: "VERSION_CONFLICT",
              message: "Load the latest case.",
              current_version: 8,
            },
          }),
          { status: 409 },
        ),
      ),
    );
    await expect(
      api("/incidents/example", "PATCH", { expected_version: 7 }),
    ).rejects.toEqual(
      expect.objectContaining({
        status: 409,
        currentVersion: 8,
        message: "Load the latest case.",
      }),
    );
  });
  it("uses memory-only CSRF, never credentials in query strings", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(new Response('{"ok":true}', { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    setCsrf("test-csrf");
    await api("/shift", "POST", { active: true });
    expect(fetcher.mock.calls[0][0]).toBe("/api/shift");
    expect(fetcher.mock.calls[0][1]).toMatchObject({
      credentials: "same-origin",
      headers: { "X-CSRF-Token": "test-csrf" },
    });
    clearSession();
    const secondFetcher = vi
      .fn()
      .mockResolvedValue(new Response('{"ok":true}'));
    vi.stubGlobal("fetch", secondFetcher);
    await api("/logout", "POST", {});
    expect(
      secondFetcher.mock.calls[0][1].headers["X-CSRF-Token"],
    ).toBeUndefined();
  });
});

describe("privacy-preserving local view lock", () => {
  it("persists only a boolean lock and requires explicit unlocking", () => {
    const store = new Map<string, string>();
    vi.stubGlobal("sessionStorage", {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => store.set(key, value),
      removeItem: (key: string) => store.delete(key),
    });
    expect(isViewLocked()).toBe(false);
    lockView(true);
    expect([...store.entries()]).toEqual([["aisle-view-locked", "true"]]);
    expect(isViewLocked()).toBe(true);
    lockView(false);
    expect(store.size).toBe(0);
  });
  it("does not throw when browser storage is unavailable", () => {
    vi.stubGlobal("sessionStorage", {
      getItem: () => {
        throw new Error("denied");
      },
      setItem: () => {
        throw new Error("denied");
      },
    });
    expect(isViewLocked()).toBe(false);
    expect(() => lockView(true)).not.toThrow();
  });
  it("retains typed errors for callers", () =>
    expect(new ApiError(401, "EXPIRED", "Sign in again.")).toBeInstanceOf(
      Error,
    ));
});

describe("late responses from a previous session", () => {
  it("cannot delete the new session retry key for the same logical payload", async () => {
    let resolveOld!: (response: Response) => void;
    const delayed = new Promise<Response>((resolve) => {
      resolveOld = resolve;
    });
    vi.stubGlobal("fetch", vi.fn().mockReturnValueOnce(delayed));
    const payload = {
      title: "Synthetic case",
      notes: "Identical test payload.",
    };
    const oldKey = idempotencyKey("/incidents", "POST", payload);
    const oldRequest = api("/incidents", "POST", payload);
    clearSession();
    setCsrf("new-session-token");
    const newKey = idempotencyKey("/incidents", "POST", payload);
    expect(newKey).not.toBe(oldKey);
    resolveOld(new Response('{"id":"old-session-resource"}', { status: 200 }));
    await oldRequest;
    expect(idempotencyKey("/incidents", "POST", payload)).toBe(newKey);
  });
});
