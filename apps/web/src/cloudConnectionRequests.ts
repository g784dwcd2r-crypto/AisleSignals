import { api, forgetAction, idempotencyKey } from "./api";

export type CloudAction =
  "prepare" | "confirm" | "pause" | "resume" | "disconnect";

/** One explicit attempt, with no credential-bearing retry fingerprint retained. */
export async function cloudConnectionRequest<T>(
  action: CloudAction,
  payload: unknown,
  signal: AbortSignal,
): Promise<T> {
  const path = `/cloud-connection/${action}`;
  const key = idempotencyKey(path, "POST", payload);
  try {
    // api constructs the request synchronously before awaiting fetch. Release
    // the shared fingerprint immediately, including while this request hangs.
    const pending = api<T>(
      path,
      "POST",
      payload,
      false,
      AbortSignal.any([signal, AbortSignal.timeout(12000)]),
    );
    forgetAction(path, "POST", payload, key);
    return await pending;
  } finally {
    forgetAction(path, "POST", payload, key);
  }
}

/** Late responses may not reopen a draft after cancellation or a context change. */
export class CloudRequestScope {
  private generation = 0;
  private controller: AbortController | null = null;

  invalidate() {
    this.generation++;
    this.controller?.abort();
    this.controller = null;
  }

  begin() {
    this.invalidate();
    const generation = this.generation;
    const controller = new AbortController();
    this.controller = controller;
    return {
      signal: controller.signal,
      current: () =>
        this.generation === generation && !controller.signal.aborted,
    };
  }
}
