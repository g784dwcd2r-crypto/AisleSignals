import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  clearSession,
  forgetAction,
  idempotencyKey,
  onSessionInvalidated,
  setSessionContext,
} from "./api";
import {
  CloudRequestScope,
  cloudConnectionRequest,
} from "./cloudConnectionRequests";

beforeEach(() => {
  clearSession();
  setSessionContext("synthetic-csrf", "synthetic-branch");
});
afterEach(() => {
  clearSession();
  onSessionInvalidated();
  vi.unstubAllGlobals();
});

it("releases password/code fingerprints while a request is still pending", async () => {
  let finish!: (response: Response) => void;
  const fetcher = vi.fn().mockImplementation(
    () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  );
  vi.stubGlobal("fetch", fetcher);
  const payload = {
    manager_password: "Synthetic password",
    code: "Synthetic one-use code",
  };
  const previous = idempotencyKey("/cloud-connection/prepare", "POST", payload);
  const pending = cloudConnectionRequest(
    "prepare",
    payload,
    new AbortController().signal,
  );
  const recreated = idempotencyKey(
    "/cloud-connection/prepare",
    "POST",
    payload,
  );
  expect(recreated).not.toBe(previous);
  forgetAction("/cloud-connection/prepare", "POST", payload, recreated);
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][0]).toBe("/api/cloud-connection/prepare");
  expect(fetcher.mock.calls[0][1].headers).toMatchObject({
    "X-CSRF-Token": "synthetic-csrf",
    "X-AisleSignals-Site": "synthetic-branch",
  });
  finish(new Response('{"ok":true}'));
  await expect(pending).resolves.toEqual({ ok: true });
});

it.each(["REAUTH_REQUIRED", "REAUTH_RATE_LIMITED"])(
  "keeps the manager session after %s without retrying",
  async (code) => {
    const invalidated = vi.fn();
    onSessionInvalidated(invalidated);
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({ error: { code, message: "Reauthenticate" } }),
          { status: code === "REAUTH_REQUIRED" ? 401 : 429 },
        ),
      );
    vi.stubGlobal("fetch", fetcher);
    await expect(
      cloudConnectionRequest(
        "pause",
        { manager_password: "Synthetic" },
        new AbortController().signal,
      ),
    ).rejects.toMatchObject({ code });
    expect(invalidated).not.toHaveBeenCalled();
    expect(fetcher).toHaveBeenCalledTimes(1);
  },
);

it.each(["offline", "incomplete"])(
  "does not retain or repeat a credential-bearing %s request",
  async (failure) => {
    const fetcher =
      failure === "offline"
        ? vi.fn().mockRejectedValue(new Error("offline"))
        : vi.fn().mockResolvedValue(new Response('{"ok":'));
    vi.stubGlobal("fetch", fetcher);
    const payload = { manager_password: "Synthetic password" };
    const key = idempotencyKey("/cloud-connection/resume", "POST", payload);
    await expect(
      cloudConnectionRequest("resume", payload, new AbortController().signal),
    ).rejects.toMatchObject({ status: 0 });
    expect(
      idempotencyKey("/cloud-connection/resume", "POST", payload),
    ).not.toBe(key);
    expect(fetcher).toHaveBeenCalledTimes(1);
  },
);

it("aborts superseded, cancelled and unmounted operations and ignores their late completions", () => {
  const scope = new CloudRequestScope();
  const first = scope.begin();
  expect(first.current()).toBe(true);
  const next = scope.begin();
  expect(first.signal.aborted).toBe(true);
  expect(first.current()).toBe(false);
  expect(next.current()).toBe(true);
  scope.invalidate();
  expect(next.signal.aborted).toBe(true);
  expect(next.current()).toBe(false);
  const remounted = scope.begin();
  expect(first.current()).toBe(false);
  expect(next.current()).toBe(false);
  expect(remounted.current()).toBe(true);
});
