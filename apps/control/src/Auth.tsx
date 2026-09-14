import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Building2,
  Check,
  LockKeyhole,
  ShieldCheck,
  Signal,
  Smartphone,
} from "lucide-react";
import { ApiError, client, isCancelled } from "./api";
import AuthenticatorQrCode from "./AuthenticatorQrCode";
import { Button, CopyValue, ErrorNotice, Field, Loading } from "./ui";
import type { Challenge, Session } from "./types";

type Mode = "login" | "setup" | "invite";
function inviteFromHash(): string {
  if (!window.location.hash.startsWith("#accept-invite?")) return "";
  const token =
    new URLSearchParams(
      window.location.hash.slice("#accept-invite?".length),
    ).get("token") || "";
  return token;
}

export default function Auth({
  authenticated,
  initialNotice,
}: {
  authenticated: (session: Session) => void;
  initialNotice?: string;
}) {
  const [inviteToken] = useState(inviteFromHash);
  const [mode, setMode] = useState<Mode>(inviteToken ? "invite" : "login");
  const [status, setStatus] = useState<{
    configured: boolean;
    needs_setup: boolean;
  } | null>(null);
  const [statusError, setStatusError] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [organisation, setOrganisation] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");
  const [code, setCode] = useState("");
  const [expiry, setExpiry] = useState(0);
  const [remaining, setRemaining] = useState(0);
  const request = useRef<AbortController | null>(null);

  useEffect(() => {
    // The initializer only reads: React StrictMode may call it twice. Remove
    // the URL capability after its in-memory state has been established.
    if (inviteToken && window.location.hash.startsWith("#accept-invite?")) {
      window.history.replaceState(
        null,
        "",
        window.location.pathname + window.location.search,
      );
    }
  }, [inviteToken]);

  useEffect(() => {
    const abort = new AbortController();
    setStatusError(null);
    client
      .get<{ configured: boolean; needs_setup: boolean }>("/setup/status", {
        public: true,
        signal: abort.signal,
      })
      .then(setStatus)
      .catch((reason: unknown) => {
        if (!isCancelled(reason)) setStatusError(reason);
      });
    return () => abort.abort();
  }, [attempt]);
  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => {
    if (!expiry) return;
    const tick = () => {
      const seconds = Math.max(0, Math.ceil((expiry - Date.now()) / 1000));
      setRemaining(seconds);
      if (seconds === 0) {
        setChallenge(null);
        setCode("");
        setExpiry(0);
        setError(
          new Error("The security challenge expired. Please start again."),
        );
      }
    };
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [expiry]);

  function reset(next: Mode) {
    request.current?.abort();
    request.current = null;
    setMode(next);
    setChallenge(null);
    setPassword("");
    setCode("");
    setToken("");
    setError(null);
    setBusy(false);
    setExpiry(0);
    setRemaining(0);
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    const abort = new AbortController();
    request.current = abort;
    setBusy(true);
    setError(null);
    try {
      if (challenge) {
        const path =
          mode === "setup"
            ? "/setup/complete"
            : mode === "invite"
              ? "/invitations/complete"
              : "/login/mfa";
        const session = await client.post<Session>(
          path,
          { challenge_token: challenge.challenge_token, code },
          { public: true, signal: abort.signal },
        );
        if (!abort.signal.aborted) {
          setChallenge(null);
          setCode("");
          setExpiry(0);
          setRemaining(0);
          authenticated(session);
        }
      } else {
        const path =
          mode === "setup"
            ? "/setup/begin"
            : mode === "invite"
              ? "/invitations/begin"
              : "/login";
        const body =
          mode === "setup"
            ? { token, organisation_name: organisation, name, email, password }
            : mode === "invite"
              ? { token: inviteToken, name, password }
              : { email, password };
        const value = await client.post<Challenge>(path, body, {
          public: true,
          signal: abort.signal,
        });
        if (!abort.signal.aborted) {
          setPassword("");
          setToken("");
          setChallenge(value);
          setCode("");
          setExpiry(Date.now() + value.expires_in_seconds * 1000);
        }
      }
    } catch (reason) {
      if (!isCancelled(reason) && !abort.signal.aborted) {
        setError(reason);
        setCode("");
        if (reason instanceof ApiError && reason.code === "CHALLENGE_EXPIRED") {
          setChallenge(null);
          setExpiry(0);
        }
      }
    } finally {
      if (!abort.signal.aborted) {
        setBusy(false);
        request.current = null;
      }
    }
  }
  const title = challenge
    ? challenge.totp_secret
      ? "Protect your account"
      : "One more security check"
    : mode === "setup"
      ? "Create your workspace"
      : mode === "invite"
        ? "Welcome to your team"
        : "Welcome back";
  return (
    <div className="auth-layout">
      <aside className="auth-story">
        <a className="brand" href="/" aria-label="AisleSignals home">
          <span className="brand-mark">
            <Signal size={24} />
          </span>
          <span>
            Aisle<span className="brand-light">Signals</span>
          </span>
        </a>
        <div className="auth-story-main">
          <span className="eyebrow">PHARMACY OPERATIONS, CONNECTED</span>
          <h1>
            A clearer picture.
            <br />
            Across every
            <br />
            <em>pharmacy.</em>
          </h1>
          <p>
            Bring your team, laptop health and incident reviews into one secure
            workspace.
          </p>
          <div className="story-points">
            <div>
              <span>
                <Building2 size={19} />
              </span>
              <section>
                <strong>One place for your group</strong>
                <p>Manage pharmacies and the people responsible for them.</p>
              </section>
            </div>
            <div>
              <span>
                <ShieldCheck size={19} />
              </span>
              <section>
                <strong>Human review stays central</strong>
                <p>Track observations, record decisions and follow up.</p>
              </section>
            </div>
            <div>
              <span>
                <LockKeyhole size={19} />
              </span>
              <section>
                <strong>Private by design</strong>
                <p>
                  CCTV stays on the laptop. This console holds operational
                  records.
                </p>
              </section>
            </div>
          </div>
        </div>
        <p className="auth-foot">AisleSignals · Pharmacy management</p>
        <div className="story-lines" aria-hidden="true">
          <i />
          <i />
          <i />
          <i />
        </div>
      </aside>
      <main className="auth-main">
        <div className="auth-mobile-brand">
          <Signal size={24} />
          AisleSignals
        </div>
        <div className="auth-card">
          <div className="security-label">
            <LockKeyhole size={15} />
            Secure workspace access
          </div>
          <h1>{title}</h1>
          <p className="auth-intro">
            {challenge
              ? challenge.totp_secret
                ? "Add this account to your authenticator app, then verify a six-digit code."
                : "Enter the current code from your authenticator app."
              : mode === "setup"
                ? "Use the private setup token supplied by your deployment administrator."
                : mode === "invite"
                  ? "Accept your private invitation and set up secure access."
                  : "Sign in to your pharmacy group. Every account uses an authenticator."}
          </p>
          {initialNotice && <div className="notice">{initialNotice}</div>}
          {Boolean(statusError) && (
            <ErrorNotice
              error={statusError}
              retry={() => setAttempt((value) => value + 1)}
            />
          )}
          {!status && !statusError && (
            <Loading text="Checking workspace availability…" />
          )}
          {status && !status.configured && (
            <div className="notice">
              This workspace is awaiting secure server configuration. Contact
              your deployment administrator.
            </div>
          )}
          {Boolean(error) && <ErrorNotice error={error} />}
          {status?.configured && (
            <form
              onSubmit={(event) => {
                void submit(event);
              }}
            >
              {challenge ? (
                <>
                  <div className="verification-icon">
                    <Smartphone size={26} />
                  </div>
                  {challenge.totp_uri && (
                    <AuthenticatorQrCode uri={challenge.totp_uri} />
                  )}
                  {challenge.totp_secret && (
                    <CopyValue
                      value={challenge.totp_secret}
                      title="Authenticator setup key"
                      hint="Keep this key private. Enter it only in your own authenticator app."
                    />
                  )}
                  <Field
                    label="Authenticator code"
                    hint="Six digits from your authenticator app."
                  >
                    <input
                      value={code}
                      onChange={(event) =>
                        setCode(
                          event.target.value.replace(/\D/g, "").slice(0, 6),
                        )
                      }
                      inputMode="numeric"
                      autoComplete="one-time-code"
                      pattern="[0-9]{6}"
                      maxLength={6}
                      required
                      autoFocus
                      className="otp-input"
                    />
                  </Field>
                  <p className="muted small">
                    {remaining > 0
                      ? `Verification expires in ${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`
                      : "Verification expired. Start again to get a new challenge."}
                  </p>
                  <Button
                    className="primary full"
                    busy={busy}
                    disabled={code.length !== 6 || remaining === 0}
                    type="submit"
                  >
                    <Check size={17} />
                    Verify and continue
                  </Button>
                  <button
                    className="text-button back-link"
                    type="button"
                    onClick={() => reset(mode)}
                  >
                    <ArrowLeft size={15} />
                    Start again
                  </button>
                </>
              ) : (
                <>
                  {mode === "setup" && (
                    <>
                      <Field label="Private setup token">
                        <input
                          type="password"
                          value={token}
                          onChange={(event) => setToken(event.target.value)}
                          required
                          autoComplete="off"
                        />
                      </Field>
                      <Field label="Pharmacy group name">
                        <input
                          value={organisation}
                          onChange={(event) =>
                            setOrganisation(event.target.value)
                          }
                          required
                          maxLength={120}
                          autoComplete="organization"
                        />
                      </Field>
                    </>
                  )}
                  {mode !== "login" && (
                    <Field label="Your full name">
                      <input
                        value={name}
                        onChange={(event) => setName(event.target.value)}
                        required
                        maxLength={120}
                        autoComplete="name"
                      />
                    </Field>
                  )}
                  {mode !== "invite" && (
                    <Field label="Work email">
                      <input
                        type="email"
                        value={email}
                        onChange={(event) => setEmail(event.target.value)}
                        required
                        maxLength={254}
                        autoComplete="username"
                      />
                    </Field>
                  )}
                  <Field
                    label={mode === "login" ? "Password" : "Create a password"}
                    hint={
                      mode !== "login"
                        ? "Use a unique password with at least 12 characters."
                        : undefined
                    }
                  >
                    <input
                      type="password"
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      required
                      minLength={mode === "login" ? undefined : 12}
                      maxLength={128}
                      autoComplete={
                        mode === "login" ? "current-password" : "new-password"
                      }
                    />
                  </Field>
                  <Button className="primary full" busy={busy} type="submit">
                    {mode === "login"
                      ? "Continue securely"
                      : "Set up authenticator"}
                    <ArrowRight size={17} />
                  </Button>
                  {mode === "login" && status.needs_setup && (
                    <p className="auth-switch">
                      First time here?{" "}
                      <button
                        type="button"
                        className="text-button"
                        onClick={() => reset("setup")}
                      >
                        Set up your workspace
                      </button>
                    </p>
                  )}
                  {mode !== "login" && (
                    <button
                      className="text-button back-link"
                      type="button"
                      onClick={() => reset("login")}
                    >
                      <ArrowLeft size={15} />
                      Back to sign in
                    </button>
                  )}
                </>
              )}
            </form>
          )}
          <div className="auth-help">
            <ShieldCheck size={16} />
            <span>
              Access is limited to the pharmacies assigned to your account.
            </span>
          </div>
        </div>
        <p className="auth-bottom">
          Need access? Ask your pharmacy group’s workspace owner.
        </p>
      </main>
    </div>
  );
}
