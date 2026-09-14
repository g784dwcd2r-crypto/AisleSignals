import { describe, expect, it, vi } from "vitest";
import { ControlClient } from "./api";

describe("account-scoped transport", () => {
  it("uses a same-origin cookie and in-memory CSRF for mutations", async () => {
    const fetcher = vi.fn(async () => new Response('{"ok":true}'));
    const api = new ControlClient(fetcher);
    api.setSession("synthetic-csrf");
    await api.post("/pharmacies", { name: "Synthetic pharmacy" });
    expect(fetcher.mock.calls[0]).toEqual([
      "/control-api/pharmacies",
      expect.objectContaining({
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error",
        headers: expect.objectContaining({ "X-CSRF-Token": "synthetic-csrf" }),
      }),
    ]);
  });

  it("rejects an old response even if its transport ignores abort", async () => {
    let finish!: (value: Response) => void;
    const fetcher = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
    const api = new ControlClient(fetcher);
    api.setSession("first-account");
    const pending = api.get("/pharmacies");
    const rejected = expect(pending).rejects.toMatchObject({
      name: "AbortError",
    });
    api.setSession("second-account");
    finish(new Response('{"items":[{"id":"private-first-account"}]}'));
    await rejected;
  });

  it("clears every active request and signals session expiry on a protected 401", async () => {
    const api = new ControlClient(
      async () => new Response('{"code":"SESSION_EXPIRED"}', { status: 401 }),
    );
    const expired = vi.fn();
    api.onUnauthorised = expired;
    api.setSession("synthetic-csrf");
    await expect(api.get("/session")).rejects.toMatchObject({ status: 401 });
    expect(expired).toHaveBeenCalledOnce();
    await expect(api.post("/pharmacies", {})).rejects.toMatchObject({
      status: 401,
    });
  });

  it("does not turn a failed public login into a second account transition", async () => {
    const api = new ControlClient(
      async () =>
        new Response('{"code":"INVALID_CREDENTIALS"}', { status: 401 }),
    );
    const expired = vi.fn();
    api.onUnauthorised = expired;
    await expect(
      api.post("/auth/login", {}, { public: true }),
    ).rejects.toMatchObject({ status: 401 });
    expect(expired).not.toHaveBeenCalled();
  });

  it("propagates component cancellation before stale data can be returned", async () => {
    const cancelled = new AbortController();
    const api = new ControlClient(async () => new Response('{"items":[]}'));
    cancelled.abort();
    await expect(
      api.get("/devices", { signal: cancelled.signal }),
    ).rejects.toMatchObject({ name: "AbortError" });
  });
});
