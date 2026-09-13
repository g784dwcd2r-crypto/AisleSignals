import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  api,
  clearSession,
  idempotencyKey,
  onSessionInvalidated,
  setSessionContext,
} from "./api";

beforeEach(() => clearSession());
afterEach(() => {
  onSessionInvalidated();
  vi.unstubAllGlobals();
});

it("scopes writes to the selected branch and rotates retry identity with the session", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation(() => Promise.resolve(new Response('{"ok":true}')));
  vi.stubGlobal("fetch", fetcher);
  setSessionContext("csrf-north", "north");
  const oldKey = idempotencyKey("/incidents", "POST", { title: "Test" });
  await api("/shift", "POST", { active: true });
  expect(fetcher.mock.calls[0][1].headers).toMatchObject({
    "X-CSRF-Token": "csrf-north",
    "X-AisleSignals-Site": "north",
  });
  setSessionContext("csrf-south", "south");
  expect(idempotencyKey("/incidents", "POST", { title: "Test" })).not.toBe(
    oldKey,
  );
  await api("/shift", "POST", { active: false });
  expect(fetcher.mock.calls[1][1].headers).toMatchObject({
    "X-CSRF-Token": "csrf-south",
    "X-AisleSignals-Site": "south",
  });
  await api("/bootstrap");
  expect(
    fetcher.mock.calls[2][1].headers["X-AisleSignals-Site"],
  ).toBeUndefined();
  clearSession();
  await api("/login", "POST", {});
  expect(
    fetcher.mock.calls[3][1].headers["X-AisleSignals-Site"],
  ).toBeUndefined();
});

it("clears capture authority on a rejected current session without invalidating a newer session", async () => {
  const invalidated = vi.fn();
  onSessionInvalidated(invalidated);
  const response = () =>
    new Response(
      JSON.stringify({
        error: { code: "SITE_CONTEXT_CHANGED", message: "Branch changed" },
      }),
      { status: 409 },
    );
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response()));
  setSessionContext("current", "north");
  await expect(api("/shift", "POST", { active: true })).rejects.toMatchObject({
    code: "SITE_CONTEXT_CHANGED",
  });
  expect(invalidated).toHaveBeenCalledTimes(1);
  let finish!: (result: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockReturnValue(
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
    ),
  );
  const old = api("/shift", "POST", { active: false });
  setSessionContext("new", "south");
  finish(response());
  await expect(old).rejects.toMatchObject({ code: "SITE_CONTEXT_CHANGED" });
  expect(invalidated).toHaveBeenCalledTimes(1);
});

it("keeps a valid manager session after failed reauthentication and releases credential retry state", async () => {
  const invalidated = vi.fn();
  onSessionInvalidated(invalidated);
  setSessionContext("current", "north");
  const payload = {
    manager_password: "Synthetic private input",
    name: "Fixture",
  };
  const previous = idempotencyKey("/admin/users", "POST", payload);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: "REAUTH_REQUIRED",
            message: "Re-enter your password",
          },
        }),
        { status: 401 },
      ),
    ),
  );
  await expect(api("/admin/users", "POST", payload)).rejects.toMatchObject({
    code: "REAUTH_REQUIRED",
  });
  expect(invalidated).not.toHaveBeenCalled();
  expect(idempotencyKey("/admin/users", "POST", payload)).not.toBe(previous);
});

it("discards sensitive retry state after a lost setup response", async () => {
  const payload = {
    password: "Synthetic initial password",
    setup_token: "synthetic-code",
  };
  const previous = idempotencyKey("/setup", "POST", payload);
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Offline")));
  await expect(api("/setup", "POST", payload)).rejects.toMatchObject({
    code: "CONNECTION_LOST",
  });
  expect(idempotencyKey("/setup", "POST", payload)).not.toBe(previous);
});
