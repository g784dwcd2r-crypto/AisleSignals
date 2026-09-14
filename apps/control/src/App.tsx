import { useEffect, useRef, useState } from "react";
import {
  Bell,
  Building2,
  ChevronDown,
  ClipboardCheck,
  LayoutDashboard,
  Laptop,
  LogOut,
  Menu,
  RefreshCw,
  ShieldCheck,
  Signal,
  Users as UsersIcon,
  WifiOff,
  X,
} from "lucide-react";
import Auth from "./Auth";
import { Pharmacies, Users } from "./Admin";
import { Alerts, Incidents, Laptops, Overview } from "./Operations";
import { ApiError, client, errorMessage, isCancelled } from "./api";
import { Button, ErrorNotice, Loading, label, useResource } from "./ui";
import type { Collection, Page, Pharmacy, Session } from "./types";
import { IdleSessionGuard, isLocallyLocked, setLocalLock } from "./idleSession";

const navigation = [
  { page: "overview", text: "Overview", icon: LayoutDashboard },
  { page: "pharmacies", text: "Pharmacies", icon: Building2 },
  { page: "laptops", text: "Laptops", icon: Laptop },
  { page: "alerts", text: "Alerts", icon: Bell },
  { page: "incidents", text: "Incidents", icon: ClipboardCheck },
  { page: "users", text: "Team & access", icon: UsersIcon },
] satisfies { page: Page; text: string; icon: typeof Bell }[];

function Console({
  session,
  logout,
  signingOut,
  logoutError,
  onIdle,
}: {
  session: Session;
  logout: () => void;
  signingOut: boolean;
  logoutError: unknown;
  onIdle: () => void;
}) {
  const [page, setPage] = useState<Page>("overview");
  const [scope, setScope] = useState("");
  const [revision, setRevision] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const [online, setOnline] = useState(navigator.onLine);
  const idleCallback = useRef(onIdle);
  idleCallback.current = onIdle;
  useEffect(() => {
    const guard = new IdleSessionGuard(() => idleCallback.current());
    const activity = () => guard.activity();
    const check = () => guard.check();
    const events = ["pointerdown", "pointermove", "keydown", "wheel"] as const;
    for (const event of events)
      window.addEventListener(event, activity, { passive: true });
    window.addEventListener("focus", check);
    document.addEventListener("visibilitychange", check);
    const timer = window.setInterval(check, 1000);
    return () => {
      clearInterval(timer);
      for (const event of events) window.removeEventListener(event, activity);
      window.removeEventListener("focus", check);
      document.removeEventListener("visibilitychange", check);
    };
  }, []);
  const pharmacies = useResource<Collection<Pharmacy>>("/pharmacies", revision);
  const changed = () => setRevision((value) => value + 1);
  useEffect(() => {
    const checkOnline = () => {
      setOnline(navigator.onLine);
      if (navigator.onLine) changed();
    };
    const visible = () => {
      if (document.visibilityState === "visible" && navigator.onLine) changed();
    };
    window.addEventListener("online", checkOnline);
    window.addEventListener("offline", checkOnline);
    document.addEventListener("visibilitychange", visible);
    const timer = window.setInterval(() => {
      if (navigator.onLine && document.visibilityState === "visible") changed();
    }, 30_000);
    return () => {
      clearInterval(timer);
      window.removeEventListener("online", checkOnline);
      window.removeEventListener("offline", checkOnline);
      document.removeEventListener("visibilitychange", visible);
    };
  }, []);
  useEffect(() => {
    if (
      scope &&
      pharmacies.data &&
      !pharmacies.data.items.some((item) => item.id === scope && item.active)
    )
      setScope("");
  }, [scope, pharmacies.data]);
  function navigate(next: Page) {
    setPage(next);
    setMenuOpen(false);
  }
  const props = {
    session,
    pharmacies: pharmacies.data?.items || session.pharmacies,
    revision,
    changed,
    scope,
  };
  const activeName = navigation.find((item) => item.page === page)?.text;
  return (
    <div className="app-shell">
      <a href="#main-content" className="skip-link">
        Skip to content
      </a>
      {menuOpen && (
        <button
          className="sidebar-backdrop"
          onClick={() => setMenuOpen(false)}
          aria-label="Close navigation"
        />
      )}
      <aside className={`sidebar ${menuOpen ? "is-open" : ""}`}>
        <div className="sidebar-brand">
          <button
            className="brand"
            onClick={() => navigate("overview")}
            aria-label="AisleSignals overview"
          >
            <span className="brand-mark">
              <Signal size={23} />
            </span>
            <span>
              Aisle<span className="brand-light">Signals</span>
            </span>
          </button>
          <button
            className="icon-button mobile-close"
            onClick={() => setMenuOpen(false)}
            aria-label="Close navigation"
          >
            <X size={20} />
          </button>
        </div>
        <div className="workspace-label">
          <span className="workspace-avatar">
            <Building2 size={19} />
          </span>
          <div>
            <strong title={session.organisation.name}>
              {session.organisation.name}
            </strong>
            <small>Pharmacy group</small>
          </div>
        </div>
        <p className="nav-label">WORKSPACE</p>
        <nav aria-label="Main navigation">
          {navigation
            .filter(
              (item) => item.page !== "users" || session.user.role === "OWNER",
            )
            .map((item) => (
              <button
                key={item.page}
                className={page === item.page ? "active" : ""}
                aria-current={page === item.page ? "page" : undefined}
                onClick={() => navigate(item.page)}
              >
                <item.icon size={19} />
                <span>{item.text}</span>
                {item.page === "overview" && (
                  <span className="nav-current-dot" />
                )}
              </button>
            ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="local-note">
            <ShieldCheck size={20} />
            <strong>Your CCTV stays local.</strong>
            <p>
              This workspace holds connection reports and reviewed operational
              records.
            </p>
          </div>
          <div className="signed-in">
            <span className="avatar">
              {session.user.name.slice(0, 1).toUpperCase()}
            </span>
            <div>
              <strong>{session.user.name}</strong>
              <small>{label(session.user.role)}</small>
            </div>
            <button
              className="icon-button"
              aria-label="Sign out"
              title="Sign out"
              onClick={logout}
              disabled={signingOut}
            >
              <LogOut size={18} />
            </button>
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-menu"
              aria-label="Open navigation"
              onClick={() => setMenuOpen(true)}
            >
              <Menu size={22} />
            </button>
            <span>Workspace</span>
            <span className="breadcrumb-divider">/</span>
            <strong>{activeName}</strong>
          </div>
          <div className="topbar-right">
            <span className="secure-session">
              <ShieldCheck size={15} />
              Protected session
            </span>
            <span className="topbar-divider" />
            <label className="scope-select">
              <Building2 size={17} />
              <select
                value={scope}
                onChange={(event) => setScope(event.target.value)}
                aria-label="Pharmacy scope"
              >
                <option value="">All permitted pharmacies</option>
                {props.pharmacies
                  .filter((pharmacy) => pharmacy.active)
                  .map((pharmacy) => (
                    <option key={pharmacy.id} value={pharmacy.id}>
                      {pharmacy.name}
                    </option>
                  ))}
              </select>
              <ChevronDown size={14} />
            </label>
            <button
              className="icon-button"
              onClick={changed}
              aria-label="Refresh workspace"
            >
              <RefreshCw size={17} />
            </button>
          </div>
        </header>
        <main id="main-content" className="main-content" tabIndex={-1}>
          {!online && (
            <div className="notice warning-notice" role="status">
              <WifiOff size={19} />
              <div>
                You are offline. Reports below may be out of date. Reconnect
                before making changes.
              </div>
            </div>
          )}
          {Boolean(logoutError) && (
            <ErrorNotice
              error="Sign-out could not be confirmed by the server. Reconnect and use Sign out again to revoke this session."
              retry={logout}
            />
          )}
          {Boolean(pharmacies.error) && (
            <ErrorNotice error={pharmacies.error} retry={pharmacies.refresh} />
          )}
          <div key={`${page}:${scope}`}>
            {page === "overview" && <Overview {...props} navigate={navigate} />}
            {page === "pharmacies" && <Pharmacies {...props} />}
            {page === "laptops" && <Laptops {...props} />}
            {page === "alerts" && <Alerts {...props} />}
            {page === "incidents" && <Incidents {...props} />}
            {page === "users" && session.user.role === "OWNER" && (
              <Users {...props} />
            )}
          </div>
          <footer className="workspace-footer">
            <span>AisleSignals · Pharmacy operations</span>
            <span>
              Refreshes every 30 seconds · Locks after 15 minutes without
              activity.
            </span>
          </footer>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  const sessionBus = useRef<BroadcastChannel | null>(null);
  const [authEpoch, setAuthEpoch] = useState(0);
  const [session, setSession] = useState<Session | null>(null);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [signingOut, setSigningOut] = useState(false);
  const [logoutError, setLogoutError] = useState<unknown>(null);
  const [acceptingInvitation] = useState(() =>
    window.location.hash.startsWith("#accept-invite?"),
  );
  useEffect(() => {
    client.onUnauthorised = () => {
      setSession(null);
      setNotice("Your previous session ended. Sign in again to continue.");
    };
    return () => {
      client.onUnauthorised = null;
      client.reset();
    };
  }, []);
  useEffect(() => {
    if (typeof BroadcastChannel === "undefined") return;
    const channel = new BroadcastChannel("aislesignals-control-session");
    sessionBus.current = channel;
    channel.onmessage = (event: MessageEvent<unknown>) => {
      if (event.data !== "identity-changed") return;
      // Cookies can change in another tab. Broadcast no account data: discard
      // the previous rows, form challenges and request continuations instead.
      client.reset();
      setLocalLock(true);
      setSession(null);
      setChecking(false);
      setError(null);
      setAuthEpoch((value) => value + 1);
      setNotice(
        "Account access changed in another tab. Sign in again to continue here.",
      );
    };
    return () => {
      sessionBus.current = null;
      channel.close();
    };
  }, []);
  useEffect(() => {
    const abort = new AbortController();
    setChecking(true);
    setError(null);
    if (acceptingInvitation || isLocallyLocked()) {
      client.reset();
      if (isLocallyLocked())
        setNotice(
          "Your workspace is locked after inactivity. Sign in again to continue.",
        );
      setChecking(false);
      return () => abort.abort();
    }
    client
      .get<Session>("/session", { public: true, signal: abort.signal })
      .then((value) => {
        if (!abort.signal.aborted) {
          client.setSession(value.csrf_token);
          setSession(value);
        }
      })
      .catch((reason: unknown) => {
        if (!abort.signal.aborted && !isCancelled(reason)) {
          if (!(reason instanceof ApiError && reason.status === 401))
            setError(reason);
        }
      })
      .finally(() => {
        if (!abort.signal.aborted) setChecking(false);
      });
    return () => abort.abort();
  }, [attempt, acceptingInvitation]);
  async function logout() {
    if (signingOut) return;
    setSigningOut(true);
    setLogoutError(null);
    try {
      await client.post("/logout");
      sessionBus.current?.postMessage("identity-changed");
      client.reset();
      setSession(null);
      setNotice("You have signed out securely.");
    } catch (reason) {
      if (!isCancelled(reason)) setLogoutError(reason);
    } finally {
      setSigningOut(false);
    }
  }
  async function lockForInactivity() {
    setLocalLock(true);
    setNotice(
      "Your workspace was locked after 15 minutes without activity. Sign in again to continue.",
    );
    setSession(null);
    setChecking(true);
    setError(null);
    // Unmounting the console aborts its requests immediately. Keep this one
    // logout request alive until it settles, then erase its in-memory CSRF.
    // No new login is offered while its cookie-deleting response is pending.
    try {
      await client.post("/logout");
    } catch {
      /* Local lock survives offline logout and page reload. */
    } finally {
      client.reset();
      sessionBus.current?.postMessage("identity-changed");
      setChecking(false);
      setSigningOut(false);
    }
  }
  if (checking)
    return (
      <div className="boot-screen">
        <div className="brand">
          <span className="brand-mark">
            <Signal size={26} />
          </span>
          AisleSignals
        </div>
        <Loading text="Opening your secure workspace…" />
      </div>
    );
  if (error && !session)
    return (
      <div className="boot-screen">
        <div className="boot-card">
          <ShieldCheck size={32} />
          <h1>We’ll get you connected.</h1>
          <p>{errorMessage(error)}</p>
          <Button
            className="primary"
            onClick={() => setAttempt((value) => value + 1)}
          >
            Try again
            <RefreshCw size={17} />
          </Button>
        </div>
      </div>
    );
  if (!session)
    return (
      <Auth
        key={authEpoch}
        initialNotice={notice}
        authenticated={(value) => {
          sessionBus.current?.postMessage("identity-changed");
          client.setSession(value.csrf_token);
          setLocalLock(false);
          setNotice("");
          setLogoutError(null);
          setSession(value);
        }}
      />
    );
  return (
    <Console
      key={`${session.organisation.id}:${session.user.id}`}
      session={session}
      logout={() => {
        void logout();
      }}
      signingOut={signingOut}
      logoutError={logoutError}
      onIdle={() => {
        void lockForInactivity();
      }}
    />
  );
}
