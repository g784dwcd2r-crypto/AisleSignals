export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
  ) {
    super(code);
    this.name = "ApiError";
  }
}

export function isCancelled(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const messages: Record<string, string> = {
      INVALID_CREDENTIALS:
        "The email or password wasn’t recognised. Please try again.",
      INVALID_MFA:
        "That authenticator code wasn’t accepted. Enter a current six-digit code.",
      CHALLENGE_EXPIRED:
        "This verification step expired. Start sign-in or setup again.",
      INVALID_INVITATION:
        "This invitation is invalid, expired or already used. Ask your workspace owner for a new one.",
      LAST_OWNER:
        "Keep at least one active workspace owner before making this change.",
      SETUP_COMPLETE:
        "This workspace has already been set up. Sign in with your own account.",
    };
    if (messages[error.code]) return messages[error.code];
    if (error.status === 401)
      return "Your session has expired. Sign in again to continue.";
    if (error.status === 403)
      return "This action is not available to your account. Refresh your session or contact your workspace owner.";
    if (error.status === 409)
      return "This record changed or already exists. Refresh and check its current details.";
    if (error.status === 429)
      return "Too many attempts. Please wait a moment before trying again.";
    if (error.status === 400 || error.status === 422)
      return "Check the details you entered and try again.";
    if (error.status === 503)
      return "The management service is starting or temporarily unavailable. Please try again shortly.";
  }
  if (error instanceof DOMException && error.name === "TimeoutError")
    return "The service is taking longer than expected. Please try again shortly.";
  return "We couldn’t reach the management service. Check your connection and try again.";
}

type RequestOptions = {
  signal?: AbortSignal;
  public?: boolean;
};

/** Account-scoped requests. Cookies stay in the browser; CSRF stays in memory. */
export class ControlClient {
  private csrf: string | null = null;
  private epoch = 0;
  private pending = new Set<AbortController>();
  onUnauthorised: (() => void) | null = null;

  constructor(
    private readonly fetcher: typeof fetch = (...args) => fetch(...args),
  ) {}

  reset(): void {
    this.epoch += 1;
    this.csrf = null;
    for (const controller of this.pending) controller.abort();
    this.pending.clear();
  }

  setSession(csrf: string): void {
    this.reset();
    this.csrf = csrf;
  }

  get<T>(path: string, options: RequestOptions = {}): Promise<T> {
    return this.request<T>("GET", path, undefined, options);
  }

  post<T>(
    path: string,
    body: unknown = {},
    options: RequestOptions = {},
  ): Promise<T> {
    return this.request<T>("POST", path, body, options);
  }

  patch<T>(
    path: string,
    body: unknown,
    options: RequestOptions = {},
  ): Promise<T> {
    return this.request<T>("PATCH", path, body, options);
  }

  private async request<T>(
    method: string,
    path: string,
    body: unknown,
    options: RequestOptions,
  ): Promise<T> {
    if (!path.startsWith("/") || path.startsWith("//") || /[\r\n]/.test(path))
      throw new Error("Invalid control endpoint");
    const epoch = this.epoch;
    const controller = new AbortController();
    const cancel = () => controller.abort(options.signal?.reason);
    if (options.signal?.aborted) cancel();
    options.signal?.addEventListener("abort", cancel, { once: true });
    this.pending.add(controller);
    const timeout = setTimeout(
      () =>
        controller.abort(new DOMException("Request timed out", "TimeoutError")),
      20_000,
    );
    const headers: Record<string, string> = { Accept: "application/json" };
    if (method !== "GET") {
      headers["Content-Type"] = "application/json";
      if (this.csrf) headers["X-CSRF-Token"] = this.csrf;
      else if (!options.public) {
        clearTimeout(timeout);
        this.pending.delete(controller);
        options.signal?.removeEventListener("abort", cancel);
        throw new ApiError(401, "SESSION_REQUIRED");
      }
    }
    try {
      const response = await this.fetcher(`/control-api${path}`, {
        method,
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error",
        headers,
        signal: controller.signal,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const raw = await response.text();
      if (controller.signal.aborted) throw controller.signal.reason;
      if (this.epoch !== epoch)
        throw new DOMException("Session changed", "AbortError");
      if (raw.length > 1_000_000) throw new ApiError(502, "RESPONSE_LIMIT");
      let value: unknown = null;
      try {
        value = raw ? JSON.parse(raw) : null;
      } catch {
        throw new ApiError(response.status || 502, "INVALID_RESPONSE");
      }
      if (!response.ok) {
        if (response.status === 401 && !options.public) {
          this.reset();
          this.onUnauthorised?.();
        }
        const envelope =
          typeof value === "object" && value !== null && "error" in value
            ? value.error
            : value;
        const code =
          typeof envelope === "object" &&
          envelope !== null &&
          "code" in envelope &&
          typeof envelope.code === "string"
            ? envelope.code
            : "REQUEST_FAILED";
        throw new ApiError(response.status, code);
      }
      return value as T;
    } finally {
      clearTimeout(timeout);
      options.signal?.removeEventListener("abort", cancel);
      this.pending.delete(controller);
    }
  }
}

export const client = new ControlClient();
