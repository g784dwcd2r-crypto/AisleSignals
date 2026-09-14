export const IDLE_LIMIT_MS = 15 * 60 * 1000;
const LOCK_KEY = "aislesignals-control-locked";

/** Activity is an explicit input; background requests never extend this clock. */
export class IdleSessionGuard {
  private lastActivity: number;
  private locked = false;
  constructor(
    private readonly lock: () => void,
    private readonly now = () => Date.now(),
  ) {
    this.lastActivity = now();
  }
  check(): boolean {
    const current = this.now();
    if (
      !this.locked &&
      (current < this.lastActivity ||
        current - this.lastActivity >= IDLE_LIMIT_MS)
    ) {
      this.locked = true;
      this.lock();
    }
    return this.locked;
  }
  activity(): void {
    if (!this.check()) this.lastActivity = this.now();
  }
}

export function isLocallyLocked(): boolean {
  try {
    return sessionStorage.getItem(LOCK_KEY) === "1";
  } catch {
    return false;
  }
}
export function setLocalLock(locked: boolean): void {
  // A boolean only: no account identifiers, CSRF, credentials or cached records.
  try {
    if (locked) sessionStorage.setItem(LOCK_KEY, "1");
    else sessionStorage.removeItem(LOCK_KEY);
  } catch {
    /* In-memory locking still applies if browser storage is unavailable. */
  }
}
