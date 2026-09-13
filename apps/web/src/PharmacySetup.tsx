import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api } from "./api";
import { adminRequest } from "./adminRequests";
import "./admin.css";

export default function PharmacySetup({
  onCreated,
}: {
  onCreated: () => Promise<void>;
}) {
  const [available, setAvailable] = useState<boolean | null>(null);
  const [organisation, setOrganisation] = useState("");
  const [branch, setBranch] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    void api<{ available: boolean }>("/setup/status")
      .then((result) => {
        if (mounted.current) setAvailable(result.available);
      })
      .catch(() => {
        if (mounted.current)
          setError(
            "Setup status could not be verified. Reload the page after checking the local service.",
          );
      });
    const clear = () => {
      if (document.hidden) {
        setPassword("");
        setConfirm("");
        setToken("");
      }
    };
    document.addEventListener("visibilitychange", clear);
    return () => {
      mounted.current = false;
      document.removeEventListener("visibilitychange", clear);
    };
  }, []);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (password !== confirm) {
      setPassword("");
      setConfirm("");
      setToken("");
      setError(
        "The two passphrases did not match. Enter the secret fields again.",
      );
      return;
    }
    setBusy(true);
    setError("");
    const payload = {
      organisation_name: organisation.trim(),
      branch_name: branch.trim(),
      name: name.trim(),
      email: email.trim(),
      password,
      setup_token: token.trim(),
    };
    setPassword("");
    setConfirm("");
    setToken("");
    try {
      await adminRequest("/setup", payload);
      if (mounted.current) await onCreated();
    } catch (failure) {
      if (mounted.current)
        setError(
          failure instanceof Error
            ? failure.message
            : "Setup could not complete. Check the local service and try again.",
        );
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  return (
    <section className="pharmacy-setup" aria-labelledby="setup-heading">
      <h3 id="setup-heading">Create the first owner account</h3>
      <p>
        The local setup operator needs the one-time setup code displayed by the
        AisleSignals launcher on this laptop. The code expires after 15 minutes.
        It is never supplied by this page.
      </p>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      {available === null ? (
        <p role="status">Checking whether this installation can be set up…</p>
      ) : !available ? (
        <div className="notice amber">
          <strong>Existing installation requires account recovery.</strong>
          <p>
            First-owner setup is unavailable because accounts or branches
            already exist. The setup operator can use the local account recovery
            command. Existing records will be preserved.
          </p>
          <code>AisleSignalsPilot accounts --help</code>
        </div>
      ) : (
        <form onSubmit={submit}>
          <label>
            Pharmacy group name
            <input
              value={organisation}
              onChange={(event) => setOrganisation(event.target.value)}
              required
              minLength={2}
              maxLength={120}
              autoComplete="organization"
            />
          </label>
          <label>
            First pharmacy branch
            <input
              value={branch}
              onChange={(event) => setBranch(event.target.value)}
              required
              minLength={2}
              maxLength={120}
            />
          </label>
          <label>
            Owner full name
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
              minLength={2}
              maxLength={120}
              autoComplete="name"
            />
          </label>
          <label>
            Owner email address
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
              maxLength={254}
              autoComplete="username"
            />
          </label>
          <label>
            Owner passphrase
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
              minLength={14}
              maxLength={256}
              autoComplete="new-password"
              aria-describedby="setup-passphrase-hint"
            />
          </label>
          <p id="setup-passphrase-hint" className="admin-hint">
            Use a unique passphrase of at least 14 characters. No password is
            prefilled or generated publicly.
          </p>
          <label>
            Confirm owner passphrase
            <input
              type="password"
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
              required
              minLength={14}
              maxLength={256}
              autoComplete="new-password"
            />
          </label>
          <label>
            One-time setup code
            <input
              type="password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              required
              maxLength={256}
              autoComplete="off"
              spellCheck={false}
            />
          </label>
          <button className="button primary full" disabled={busy}>
            {busy ? "Creating owner account…" : "Create owner account"}
          </button>
          <button
            className="button secondary full"
            type="button"
            disabled={busy}
            onClick={() => {
              setPassword("");
              setConfirm("");
              setToken("");
              setError("");
            }}
          >
            Clear secret fields
          </button>
        </form>
      )}
    </section>
  );
}
