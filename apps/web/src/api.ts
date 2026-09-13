export class ApiError extends Error {
  status: number;
  code: string;
  currentVersion?: number;
  constructor(
    status: number,
    code: string,
    message: string,
    currentVersion?: number,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.currentVersion = currentVersion;
  }
}
let csrfToken = "";
export function isViewLocked() {
  try {
    return sessionStorage.getItem("aisle-view-locked") === "true";
  } catch {
    return false;
  }
}
export function lockView(locked: boolean) {
  try {
    if (locked) sessionStorage.setItem("aisle-view-locked", "true");
    else sessionStorage.removeItem("aisle-view-locked");
  } catch {
    /* A storage-restricted browser will still clear this view in memory. */
  }
}
const pendingKeys = new Map<string, string>();
export function setCsrf(value: string) {
  csrfToken = value;
}
export function clearSession() {
  csrfToken = "";
  pendingKeys.clear();
}
export function actionFingerprint(
  path: string,
  method: string,
  payload: unknown,
) {
  return `${method}:${path}:${JSON.stringify(payload)}`;
}
export function idempotencyKey(path: string, method: string, payload: unknown) {
  const fingerprint = actionFingerprint(path, method, payload);
  let key = pendingKeys.get(fingerprint);
  if (!key) {
    key = crypto.randomUUID();
    pendingKeys.set(fingerprint, key);
  }
  return key;
}
export function forgetAction(
  path: string,
  method: string,
  payload: unknown,
  completedKey: string,
) {
  const fingerprint = actionFingerprint(path, method, payload);
  if (pendingKeys.get(fingerprint) === completedKey)
    pendingKeys.delete(fingerprint);
}
export async function api<T>(
  path: string,
  method = "GET",
  payload?: unknown,
  download = false,
): Promise<T> {
  const write = method !== "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (payload !== undefined) headers["Content-Type"] = "application/json";
  if (write && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  if (write && path !== "/login")
    headers["Idempotency-Key"] = idempotencyKey(path, method, payload);
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers,
      credentials: "same-origin",
      cache: "no-store",
      body: payload === undefined ? undefined : JSON.stringify(payload),
      signal: AbortSignal.timeout(12000),
    });
  } catch {
    throw new ApiError(
      0,
      "CONNECTION_LOST",
      "Connection lost. Your unsaved input is kept. Reconnect and retry the same action.",
    );
  }
  if (!response.ok) {
    let error;
    try {
      error = (await response.json()).error;
    } catch {
      /* HTTP failure without JSON */
    }
    throw new ApiError(
      response.status,
      error?.code ?? "REQUEST_FAILED",
      error?.message ?? `Request failed (${response.status}).`,
      error?.current_version,
    );
  }
  let result: T;
  try {
    result = (download ? await response.blob() : await response.json()) as T;
  } catch {
    throw new ApiError(
      0,
      "INCOMPLETE_RESPONSE",
      "The server response was incomplete. Your input and retry key are kept. Reconnect and retry the same action.",
    );
  }
  const completedKey = headers["Idempotency-Key"];
  if (write && completedKey) forgetAction(path, method, payload, completedKey);
  return result;
}
export function safeEvidenceUrl(value: string | null): string | null {
  // Only this application's authenticated synthetic media route is displayable.
  return value && /^\/api\/evidence\/[a-f0-9-]{36}$/i.test(value)
    ? value
    : null;
}
