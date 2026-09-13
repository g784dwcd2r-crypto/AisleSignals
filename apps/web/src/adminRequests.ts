import { api, forgetAction, idempotencyKey } from "./api";

/** Sensitive requests never leave a retry fingerprint containing a password. */
export async function adminRequest<T>(
  path: string,
  payload: unknown,
): Promise<T> {
  const key = idempotencyKey(path, "POST", payload);
  try {
    return await api<T>(path, "POST", payload);
  } finally {
    forgetAction(path, "POST", payload, key);
  }
}
