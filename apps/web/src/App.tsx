import { useSyncExternalStore } from "react";
import { runtimeHealth, watchRuntimeHealth } from "./runtimeHealth";
import type { RuntimeHealthReport } from "./runtimeHealth";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { flushSync } from "react-dom";
import "./pilot.css";
import type { FormEvent, ReactNode } from "react";
import {
  Activity,
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  Bell,
  Check,
  CheckCheck,
  ChevronRight,
  CircleHelp,
  Cloud,
  ClipboardList,
  FileText,
  Film,
  LayoutDashboard,
  LogOut,
  Menu,
  Monitor,
  Pause,
  Play,
  Plus,
  Radio,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Video,
  Volume2,
  WifiOff,
  X,
} from "lucide-react";
import type {
  Assistance,
  Bootstrap,
  Camera,
  Candidate,
  Classification,
  Incident,
  InteractionCaseSource,
  Outcome,
  Page,
  Runtime,
  Session,
  User,
} from "./types";
import {
  api,
  ApiError,
  clearSession,
  isViewLocked,
  lockView,
  onSessionInvalidated,
  safeEvidenceUrl,
  setSessionContext,
} from "./api";
import { date, getAlertCount, label, money } from "./format";
import { safeInteractionFrameUrl } from "./interactionCapture";
import CameraContextDetails from "./CameraContextDetails";

import { buildCasePatch, caseFields } from "./caseForm";
import type { CaseForm } from "./caseForm";
import VideoTest from "./VideoTest";
import LiveDetection from "./LiveDetection";
import PharmacyAdmin from "./PharmacyAdmin";
import PharmacySetup from "./PharmacySetup";
import CloudConnection from "./CloudConnection";

type ActionOptions = {
  method?: string;
  success?: string;
  download?: boolean;
  refresh?: boolean;
};
type Act = <T>(
  path: string,
  payload: unknown,
  options?: ActionOptions,
) => Promise<T | null>;
const navItems: { id: Page; name: string; icon: typeof Activity }[] = [
  { id: "overview", name: "Overview", icon: LayoutDashboard },
  { id: "live-detection", name: "LIVE DETECTION", icon: Monitor },
  { id: "review", name: "Review queue", icon: Radio },
  { id: "incidents", name: "Casebook", icon: ClipboardList },
  { id: "assistance", name: "Team assistance", icon: Bell },
  { id: "cameras", name: "Camera readiness", icon: Video },
  { id: "video-test", name: "Video test", icon: Film },
  { id: "activity", name: "Activity log", icon: Activity },
  { id: "settings", name: "Branch settings", icon: Settings2 },
  { id: "administration", name: "Administration", icon: ShieldCheck },
  { id: "cloud-connection", name: "Cloud connection", icon: Cloud },
];

function Mark() {
  return (
    <svg
      width="28"
      height="28"
      viewBox="0 0 28 28"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M4 21V13M10.5 23V6M17 21V10M23.5 16V4"
        stroke="currentColor"
        strokeWidth="3.5"
        strokeLinecap="round"
      />
    </svg>
  );
}
function Badge({ value, children }: { value?: string; children?: ReactNode }) {
  return (
    <span className={`badge badge-${value?.toLowerCase() ?? "neutral"}`}>
      {children ?? label(value ?? "")}
    </span>
  );
}
function Empty({
  title,
  detail,
  children,
}: {
  title: string;
  detail: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <CheckCheck size={26} />
      </div>
      <h3>{title}</h3>
      <p>{detail}</p>
      {children}
    </div>
  );
}
function Panel({
  title,
  description,
  action,
  children,
  className = "",
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      <div className="panel-head">
        <div>
          <h2>{title}</h2>
          {description && <p>{description}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
function useDraft<T>(_key: string, initial: T) {
  return useState<T>(initial);
}
function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const frame = requestAnimationFrame(() =>
      ref.current
        ?.querySelector<HTMLElement>("input,textarea,select,button")
        ?.focus(),
    );
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "Tab") {
        const list = Array.from(
          ref.current?.querySelectorAll<HTMLElement>(
            "button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),a[href]",
          ) ?? [],
        );
        const first = list[0],
          last = list.at(-1);
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    const old = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = old;
      previous?.focus();
    };
  }, [onClose]);
  return (
    <div className="modal-backdrop">
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        ref={ref}
      >
        <div className="modal-header">
          <h2>{title}</h2>
          <button
            className="icon-button"
            aria-label="Close dialog"
            onClick={onClose}
          >
            <X size={21} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
const PilotContext = createContext(false);
function Login({
  runtime,
  onLogin,
  onSetupCreated,
}: {
  runtime: Runtime;
  onLogin: (session: Session) => void;
  onSetupCreated: () => Promise<void>;
}) {
  const pilot = runtime.mode === "pilot";
  const [email, setEmail] = useState(pilot ? "" : "manager@harbour.demo");
  const [password, setPassword] = useState(pilot ? "" : "AisleDemo!2026");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const result = await api<Session>("/login", "POST", { email, password });
      setPassword("");
      lockView(false);
      onLogin(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="login-layout">
      <aside className="login-story">
        <div className="brand">
          <Mark />
          <span>
            AisleSignals<span className="brand-dot">.</span>
          </span>
        </div>
        <div className="login-story-content">
          <span className="eyebrow light">BUILT AROUND YOUR PHARMACY</span>
          <h1>
            A little more clarity.
            <br />A calmer working day.
          </h1>
          <p>
            One considered workspace to review observations, support colleagues
            and keep a clear record.
          </p>
          <div className="story-lines" aria-hidden="true">
            <span />
            <span />
            <span />
            <span />
            <span />
            <span />
            <span />
            <span />
            <span />
          </div>
        </div>
        <p className="login-footer">
          Designed for pharmacies in Ireland · Jawahir Q.
        </p>
      </aside>
      <main className="login-main">
        <div className="login-card">
          <div className="prototype-label">
            <span />{" "}
            {pilot ? "LOCAL PHARMACY WORKSPACE" : "SYNTHETIC PROTOTYPE · 0.1"}
          </div>
          <h2>Welcome to your workspace.</h2>
          <p className="lead">
            {pilot
              ? "Sign in with your individual account to access your assigned pharmacy branches on this laptop."
              : "Explore the complete review-to-record workflow with clearly labelled demonstration data."}
          </p>
          {pilot && runtime.setup_required ? (
            <PharmacySetup onCreated={onSetupCreated} />
          ) : (
            <form onSubmit={submit}>
              {!pilot && (
                <label>
                  Demo account
                  <select
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                  >
                    <option value="manager@harbour.demo">
                      Harbour Pharmacy · Manager
                    </option>
                    <option value="reviewer@harbour.demo">
                      Harbour Pharmacy · Reviewer
                    </option>
                    <option value="manager@liffey.demo">
                      Liffey Pharmacy · Manager
                    </option>
                    <option value="manager@marimina.demo">
                      Mari Mina Pharmacy · Manager
                    </option>
                  </select>
                </label>
              )}
              <label>
                Email address
                <input
                  autoComplete="username"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                />
              </label>
              <label>
                Password
                <input
                  autoComplete="current-password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
              </label>
              {error && (
                <div className="inline-error" role="alert">
                  {error}
                </div>
              )}
              <button
                className="button primary full"
                disabled={busy || (pilot && runtime.setup_required)}
              >
                {busy
                  ? "Opening workspace…"
                  : pilot
                    ? "Sign in"
                    : "Open demo workspace"}
                <ArrowRight size={18} />
              </button>
            </form>
          )}
          {pilot ? (
            <div className="pilot-login-note">
              <ShieldCheck size={19} />
              <p>
                Individual local accounts. Records stay in this installation;
                branch access does not synchronise other laptops. Contact your
                setup operator for account access or a password reset.
              </p>
            </div>
          ) : (
            <div className="demo-credentials">
              <ShieldCheck size={19} />
              <div>
                <strong>Public demo credentials</strong>
                <span>{email}</span>
                <code>AisleDemo!2026</code>
                <p>
                  Demo accounts and synthetic workflow records. LIVE DETECTION
                  processes an explicitly selected video locally, with laptop
                  attention sounds. No external alarms or billing.
                </p>
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

export default function App() {
  const health = useSyncExternalStore(
    runtimeHealth.subscribe,
    runtimeHealth.snapshot,
  );
  const [runtime, setRuntime] = useState<Runtime | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [startupError, setStartupError] = useState("");
  const [switching, setSwitching] = useState(false);
  const [user, setUser] = useState<User | null>(null);
  const [data, setData] = useState<Bootstrap | null>(null);
  const [starting, setStarting] = useState(true);
  const [page, setPage] = useState<Page>(
    location.hash === "#live-detection" ? "live-detection" : "overview",
  );
  const [candidateId, setCandidateId] = useState<string | null>(null);
  const [incidentId, setIncidentId] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState<{
    message: string;
    conflict: boolean;
  } | null>(null);
  const [toast, setToast] = useState("");
  const [busy, setBusy] = useState(false);
  const [menu, setMenu] = useState(false);
  const [modal, setModal] = useState<
    "manual" | "assistance" | "simulator" | null
  >(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const mounted = useRef(true);
  const requestEpoch = useRef(0);
  const authGeneration = useRef(0);
  const tabId = useRef(crypto.randomUUID());
  const siteRef = useRef("");
  const userRef = useRef<User | null>(null);
  const stopHealthWatch = useRef<() => void>(() => {});
  userRef.current = user;
  const endSession = useCallback(() => {
    runtimeHealth.end();
    authGeneration.current++;
    requestEpoch.current++;
    userRef.current = null;
    siteRef.current = "";
    setSession(null);
    setSwitching(false);
    setBusy(false);
    setToast("");
    setMenu(false);
    setUser(null);
    setData(null);
    setModal(null);
    setCandidateId(null);
    setIncidentId(null);
    setPage("overview");
    clearSession();
  }, []);
  const refresh = useCallback(async () => {
    const generation = authGeneration.current;
    const epoch = ++requestEpoch.current;
    try {
      const result = await api<Bootstrap>("/bootstrap");
      if (
        !mounted.current ||
        generation !== authGeneration.current ||
        epoch !== requestEpoch.current ||
        !userRef.current
      )
        return;
      if (siteRef.current && result.site.id !== siteRef.current) {
        endSession();
        setError({
          message:
            "The active branch changed in another tab. Sign in again to continue in the correct branch.",
          conflict: false,
        });
        return;
      }
      setData(result);
      setOffline(false);
      return true;
    } catch (err) {
      if (
        !mounted.current ||
        generation !== authGeneration.current ||
        epoch !== requestEpoch.current
      )
        return;
      if (err instanceof ApiError && err.status === 401) {
        endSession();
        setError({
          message: "Your session ended. Sign in again to continue.",
          conflict: false,
        });
      } else {
        setOffline(true);
        setError({
          message: err instanceof Error ? err.message : "Unable to connect.",
          conflict: false,
        });
      }
    }
  }, [endSession]);
  const acceptSession = useCallback((result: Session) => {
    runtimeHealth.begin(result.current_site_id);
    authGeneration.current++;
    requestEpoch.current++;
    userRef.current = result.user;
    siteRef.current = result.current_site_id;
    setSessionContext(result.csrf_token, result.current_site_id);
    setSession(result);
    setUser(result.user);
    setBusy(false);
    setError(null);
    setOffline(false);
  }, []);
  useEffect(() => {
    if (!session) {
      stopHealthWatch.current = () => {};
      return;
    }
    const stop = watchRuntimeHealth(runtimeHealth, (signal) =>
      api<RuntimeHealthReport>(
        "/runtime/health",
        "GET",
        undefined,
        false,
        signal,
      ),
    );
    stopHealthWatch.current = stop;
    return () => {
      if (stopHealthWatch.current === stop) stopHealthWatch.current = () => {};
      stop();
    };
  }, [session]);
  useEffect(() => {
    onSessionInvalidated(() => {
      endSession();
      setError({
        message:
          "Your session or branch access changed. Monitoring stopped and this view was cleared. Sign in again to continue.",
        conflict: false,
      });
    });
    return () => onSessionInvalidated();
  }, [endSession]);
  useEffect(() => {
    mounted.current = true;
    let cancelled = false;
    async function start() {
      try {
        const config = await api<Runtime>("/runtime");
        if (cancelled) return;
        setRuntime(config);
        if (!isViewLocked()) {
          try {
            const existing = await api<Session>("/session");
            if (!cancelled) acceptSession(existing);
          } catch (err) {
            if (!(err instanceof ApiError && err.status === 401)) throw err;
          }
        }
      } catch {
        if (!cancelled)
          setStartupError(
            "The local service is unavailable. Start AisleSignals on this laptop, then retry.",
          );
      } finally {
        if (!cancelled) setStarting(false);
      }
    }
    void start();
    return () => {
      cancelled = true;
      mounted.current = false;
    };
  }, [acceptSession]);
  useEffect(() => {
    if (runtime?.mode !== "pilot" || typeof BroadcastChannel === "undefined")
      return;
    const channel = new BroadcastChannel("aislesignals-local-session");
    channel.onmessage = (event) => {
      if (event.data?.tabId === tabId.current || !userRef.current) return;
      endSession();
      setError({
        message:
          "The session changed in another tab. Monitoring stopped and this view was cleared. Sign in again to continue.",
        conflict: false,
      });
    };
    return () => channel.close();
  }, [runtime?.mode, endSession]);
  function notifyOtherTabs() {
    if (runtime?.mode !== "pilot" || typeof BroadcastChannel === "undefined")
      return;
    const channel = new BroadcastChannel("aislesignals-local-session");
    channel.postMessage({ tabId: tabId.current });
    channel.close();
  }
  async function switchBranch(siteId: string) {
    if (busy || switching || offline || !session || siteId === siteRef.current)
      return;
    const generation = ++authGeneration.current;
    requestEpoch.current++;
    // Stop the old branch heartbeat before the server rotates the session
    // cookie and CSRF token. Otherwise a late old-scope 401 can invalidate the
    // newly returned branch session on a slower host.
    stopHealthWatch.current();
    runtimeHealth.end();
    // Tear down capture, pending review forms and media before changing server scope.
    flushSync(() => {
      setSwitching(true);
      setBusy(true);
      setData(null);
      setModal(null);
      setCandidateId(null);
      setIncidentId(null);
      setToast("");
      setError(null);
      setPage("overview");
    });
    try {
      const next = await api<Session>("/session/site", "POST", {
        site_id: siteId,
      });
      if (!mounted.current || generation !== authGeneration.current) return;
      notifyOtherTabs();
      acceptSession(next);
      setSwitching(false);
      history.replaceState(null, "", `${location.pathname}${location.search}`);
    } catch {
      if (!mounted.current || generation !== authGeneration.current) return;
      lockView(true);
      endSession();
      setError({
        message:
          "The branch switch could not be confirmed. Monitoring stopped and this view was cleared. Sign in again to select the correct branch.",
        conflict: false,
      });
    }
  }
  useEffect(() => {
    if (!user || switching) return;
    void refresh();
    const timer = setInterval(() => void refresh(), 10000);
    return () => clearInterval(timer);
  }, [user, switching, refresh]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 5500);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    headingRef.current?.focus();
  }, [page]);
  const act: Act = async <T,>(
    path: string,
    payload: unknown,
    options: ActionOptions = {},
  ) => {
    if (offline || busy || !userRef.current) return null;
    const generation = authGeneration.current;
    const isCurrentSession = () =>
      mounted.current && generation === authGeneration.current;
    setBusy(true);
    setError(null);
    try {
      const result = await api<T>(
        path,
        options.method ?? "POST",
        payload,
        options.download,
      );
      if (!isCurrentSession()) return null;
      if (options.refresh !== false) await refresh();
      if (!isCurrentSession()) return null;
      if (options.success) setToast(options.success);
      return result;
    } catch (err) {
      if (!isCurrentSession()) return null;
      if (err instanceof ApiError && err.status === 401) {
        endSession();
      }
      if (err instanceof ApiError && err.status === 0) setOffline(true);
      setError({
        message:
          err instanceof Error
            ? err.message
            : "The action could not be completed.",
        conflict: err instanceof ApiError && err.status === 409,
      });
      return null;
    } finally {
      if (isCurrentSession()) setBusy(false);
    }
  };
  function navigate(next: Page) {
    setPage(next);
    history.replaceState(
      null,
      "",
      `${location.pathname}${location.search}${next === "live-detection" ? "#live-detection" : ""}`,
    );
    setMenu(false);
    setCandidateId(null);
    setIncidentId(null);
    setError(null);
  }
  function openCandidate(id: string) {
    setCandidateId(id);
    setPage("review");
  }
  function openIncident(id: string) {
    setIncidentId(id);
    setPage("incidents");
  }
  async function openInteractionCase(id: string) {
    const generation = authGeneration.current;
    if (
      (await refresh()) &&
      mounted.current &&
      generation === authGeneration.current
    )
      openIncident(id);
  }
  const closeModal = useCallback(() => setModal(null), []);
  if (starting)
    return (
      <div className="startup">
        <Mark />
        <p>Opening AisleSignals…</p>
      </div>
    );
  if (startupError || !runtime)
    return (
      <div className="startup">
        <Mark />
        <p role="alert">
          {startupError || "Unable to identify this installation."}
        </p>
        <button className="button primary" onClick={() => location.reload()}>
          Retry local service
        </button>
      </div>
    );
  if (!user)
    return (
      <>
        {error && (
          <div className="login-notice" role="status">
            {error.message}
          </div>
        )}
        <Login
          key={`${runtime.mode}:${runtime.setup_required}`}
          runtime={runtime}
          onSetupCreated={async () => {
            const config = await api<Runtime>("/runtime");
            setRuntime(config);
            setError({
              message:
                "Owner account created. Sign in with the individual account you just created.",
              conflict: false,
            });
          }}
          onLogin={(result) => {
            notifyOtherTabs();
            acceptSession(result);
          }}
        />
      </>
    );
  const pilot = runtime.mode === "pilot";
  const disabled = offline || busy || switching;
  const manager = user.role === "MANAGER";
  const title = navItems.find((item) => item.id === page)!.name;
  return (
    <PilotContext.Provider value={pilot}>
      <div className="app-shell">
        <a className="skip-link" href="#main-content">
          Skip to content
        </a>
        {menu && (
          <button
            className="sidebar-scrim"
            aria-label="Close navigation"
            onClick={() => setMenu(false)}
          />
        )}
        <aside className={`sidebar ${menu ? "sidebar-open" : ""}`}>
          <a
            className="brand"
            href="/"
            onClick={(e) => {
              e.preventDefault();
              navigate("overview");
            }}
          >
            <Mark />
            <span>
              AisleSignals<span className="brand-dot">.</span>
            </span>
          </a>
          <div className="branch-switch">
            <div className="branch-avatar">{data?.site.name[0] ?? "P"}</div>
            <div>
              <strong>{data?.site.name ?? "Your pharmacy"}</strong>
              <span>Pharmacy workspace</span>
            </div>
            <ShieldCheck size={16} />
          </div>
          {pilot && session && (
            <div className="pilot-branch-picker">
              <label htmlFor="active-pharmacy">Active pharmacy branch</label>
              <select
                id="active-pharmacy"
                value={session.current_site_id}
                disabled={disabled}
                onChange={(event) => void switchBranch(event.target.value)}
              >
                {session.allowed_sites.map((site) => (
                  <option key={site.id} value={site.id}>
                    {site.name} · {label(site.role)}
                  </option>
                ))}
              </select>
              <p>Switching stops monitoring and clears unsaved work.</p>
            </div>
          )}
          <span className="nav-section-label">WORKSPACE</span>
          <nav aria-label="Main navigation">
            {navItems
              .filter(
                (item) =>
                  !["administration", "cloud-connection"].includes(item.id) ||
                  (pilot && manager),
              )
              .map(({ id, name, icon: Icon }) => (
                <button
                  key={id}
                  className={`nav-item ${page === id ? "active" : ""}`}
                  aria-current={page === id ? "page" : undefined}
                  onClick={() => navigate(id)}
                >
                  <Icon size={19} />
                  <span>{name}</span>
                  {id === "review" &&
                    data &&
                    getAlertCount(data.candidates) > 0 && (
                      <span className="nav-count">
                        {getAlertCount(data.candidates)}
                      </span>
                    )}
                </button>
              ))}
          </nav>
          <div className="sidebar-note">
            <div className="leaf-icon">
              <ShieldCheck size={21} />
            </div>
            <strong>People make the decisions.</strong>
            <p>
              Observations support your team. Every case is reviewed by a
              person.
            </p>
          </div>
          <div className="sidebar-bottom">
            <span className="prototype-label">
              <span /> {pilot ? "LOCAL PILOT" : "SYNTHETIC PROTOTYPE"}
            </span>
            <div className="user-box">
              <div className="user-avatar">
                {user.name
                  .split(" ")
                  .map((s) => s[0])
                  .slice(0, 2)
                  .join("")}
              </div>
              <div>
                <strong>{user.name}</strong>
                <span>{label(user.role)}</span>
              </div>
              <button
                className="icon-button"
                title="Sign out"
                aria-label="Sign out"
                disabled={busy}
                onClick={async () => {
                  if (offline) {
                    lockView(true);
                    endSession();
                    setError({
                      message:
                        "View locked and local records cleared. The server session could not be revoked while offline; sign in explicitly when connected.",
                      conflict: false,
                    });
                    return;
                  }
                  const generation = authGeneration.current;
                  const result = await act("/logout", {}, { refresh: false });
                  if (generation !== authGeneration.current) return;
                  lockView(!result);
                  notifyOtherTabs();
                  endSession();
                  if (!result)
                    setError({
                      message:
                        "View locked and local records cleared. Server session revocation was not confirmed.",
                      conflict: false,
                    });
                }}
              >
                <LogOut size={18} />
              </button>
            </div>
          </div>
        </aside>
        <div className="workspace">
          <header className="topbar">
            <div className="breadcrumb">
              <button
                className="icon-button mobile-menu"
                aria-label="Open navigation"
                onClick={() => setMenu(true)}
              >
                <Menu size={22} />
              </button>
              <span>Workspace</span>
              <ChevronRight size={14} />
              <strong>{title}</strong>
            </div>
            <div className="topbar-right">
              <span className={`coverage-status ${offline ? "unknown" : ""}`}>
                <span />
                {offline
                  ? "Connection lost · coverage unknown"
                  : page === "live-detection"
                    ? "Local pose analysis · experimental rules"
                    : page === "video-test"
                      ? "Playback test · no live monitoring"
                      : pilot
                        ? "Local workspace · open LIVE DETECTION to monitor"
                        : "Synthetic data · no live monitoring"}
              </span>
              <button
                className="icon-button"
                aria-label="Refresh workspace"
                title="Refresh workspace"
                onClick={() => void refresh()}
              >
                <RefreshCw size={17} />
              </button>
              <span className="topbar-divider" />
              <span className="timezone">
                Ireland <span>·</span>{" "}
                {new Intl.DateTimeFormat("en-IE", {
                  timeZone: "Europe/Dublin",
                  day: "numeric",
                  month: "short",
                }).format(new Date())}
              </span>
            </div>
          </header>
          <main id="main-content" tabIndex={-1} className="main-content">
            {error && (
              <div
                className={`error-banner ${error.conflict ? "conflict-banner" : ""}`}
                role="alert"
              >
                <div>
                  {offline ? <WifiOff size={19} /> : <CircleHelp size={19} />}
                  <span>
                    <strong>
                      {error.conflict
                        ? "This record has changed. "
                        : offline
                          ? "Connection unavailable. "
                          : ""}
                    </strong>
                    {error.message}
                    {error.conflict &&
                      " Refresh to view the latest record. Your unsaved fields remain until you choose to load that record."}
                  </span>
                </div>
                <button className="text-button" onClick={() => void refresh()}>
                  Retry connection
                </button>
                <button
                  className="icon-button"
                  aria-label="Dismiss message"
                  onClick={() => setError(null)}
                >
                  <X size={17} />
                </button>
              </div>
            )}
            {health.active && (!health.ready || health.interrupted) && (
              <div
                className="notice amber"
                role="status"
                aria-label="Runtime monitoring health"
              >
                {health.message} Last loaded cases remain available; records may
                need refreshing.
              </div>
            )}
            {offline && (
              <div className="notice amber">
                Actions are paused. Last loaded records may be out of date;
                coverage is unknown.
              </div>
            )}
            {page !== "live-detection" && (
              <div className="page-heading">
                <div>
                  <div className="eyebrow">
                    {page === "overview"
                      ? "YOUR PHARMACY, IN FOCUS"
                      : page === "review"
                        ? "OBSERVE · REVIEW · DECIDE"
                        : "A CLEARER WORKING DAY"}
                  </div>
                  <h1 ref={headingRef} tabIndex={-1}>
                    {page === "overview"
                      ? "Every detail, thoughtfully handled."
                      : title}
                  </h1>
                  <p>
                    {
                      {
                        overview:
                          "A clear view of what needs attention, and a record of what happens next.",
                        review:
                          "An observation is a prompt to review. Your team determines the outcome.",
                        incidents:
                          "One place for reviewed facts, follow-up tasks and recorded outcomes.",
                        assistance:
                          "Ask a colleague for support and track their response.",
                        cameras:
                          "Understand the connection before relying on the coverage.",
                        "video-test":
                          "Try a recording locally and review a timeline of visual activity.",
                        "live-detection":
                          "Connect your CCTV video. Follow activity. Receive an automatic attention alarm.",
                        activity:
                          "A traceable record of actions within this pharmacy branch.",
                        settings: pilot
                          ? "Your branch, account access and local installation details."
                          : "Your branch, subscription terms and prototype boundaries.",
                        administration:
                          "Manage individual accounts and branch access on this installation.",
                        "cloud-connection":
                          "Review this branch’s online connection and control metadata sharing.",
                      }[page]
                    }
                  </p>
                </div>
                <div className="page-actions">
                  {page !== "assistance" && page !== "video-test" && (
                    <button
                      className="button secondary"
                      disabled={disabled}
                      onClick={() => setModal("assistance")}
                    >
                      <Bell size={17} />
                      Ask for assistance
                    </button>
                  )}
                  {page === "incidents" || page === "overview" ? (
                    <button
                      className="button primary"
                      disabled={disabled}
                      onClick={() => setModal("manual")}
                    >
                      <Plus size={17} />
                      New manual case
                    </button>
                  ) : null}
                </div>
              </div>
            )}
            {!data ? (
              <div className="panel">
                <Empty
                  title={
                    switching
                      ? "Switching pharmacy branch"
                      : offline
                        ? "Waiting for connection"
                        : "Loading your workspace"
                  }
                  detail={
                    offline
                      ? "Your session is signed in. Retry when the local service is available."
                      : "Fetching this pharmacy’s records…"
                  }
                />
              </div>
            ) : (
              <>
                {page === "overview" && (
                  <Overview
                    data={data}
                    manager={manager}
                    disabled={disabled}
                    act={act}
                    navigate={navigate}
                    openCandidate={openCandidate}
                    openIncident={openIncident}
                    simulator={() => setModal("simulator")}
                  />
                )}
                {page === "review" && (
                  <Review
                    data={data}
                    selectedId={candidateId}
                    select={setCandidateId}
                    openIncident={openIncident}
                    disabled={disabled}
                    act={act}
                    manager={manager}
                    simulator={() => setModal("simulator")}
                  />
                )}
                {page === "incidents" && (
                  <Casebook
                    data={data}
                    selectedId={incidentId}
                    select={setIncidentId}
                    disabled={disabled}
                    act={act}
                    manager={manager}
                    openCandidate={openCandidate}
                    create={() => setModal("manual")}
                  />
                )}
                {page === "assistance" && (
                  <AssistancePage
                    data={data}
                    disabled={disabled}
                    act={act}
                    create={() => setModal("assistance")}
                  />
                )}
                {page === "cameras" && (
                  <Cameras
                    data={data}
                    manager={manager}
                    disabled={disabled}
                    simulator={() => setModal("simulator")}
                  />
                )}
                {page === "video-test" && (
                  <VideoTest
                    key={`${user.id}:${data.site.id}`}
                    branchName={data.site.name}
                  />
                )}
                {page === "live-detection" && (
                  <LiveDetection
                    key={`${user.id}:${data.site.id}`}
                    branchName={data.site.name}
                    branchId={data.site.id}
                    organisationId={
                      session?.allowed_sites.find(
                        (site) => site.id === data.site.id,
                      )?.organisation_id
                    }
                    onOpenInteractionCase={openInteractionCase}
                  />
                )}
                {page === "activity" && <ActivityPage data={data} />}
                {page === "settings" && <Settings data={data} />}
                {page === "cloud-connection" && pilot && manager && (
                  <CloudConnection
                    key={`${user.id}:${data.site.id}`}
                    branchId={data.site.id}
                    branchName={data.site.name}
                    disabled={disabled}
                  />
                )}
                {page === "administration" && pilot && manager && session && (
                  <PharmacyAdmin
                    key={`${user.id}:${data.site.id}`}
                    user={user}
                    siteId={data.site.id}
                    disabled={disabled}
                    onAccessChanged={async () => {
                      const next = await api<Session>("/session");
                      acceptSession(next);
                    }}
                    onOwnAccountChanged={() => {
                      notifyOtherTabs();
                      endSession();
                      setError({
                        message:
                          "Your account access changed. Sign in again to continue.",
                        conflict: false,
                      });
                    }}
                  />
                )}
                <div className="workspace-footnote">
                  <ShieldCheck size={14} />
                  <span>
                    {page === "live-detection"
                      ? "Video stays on this laptop · Review every alert · Stop detection before leaving the CCTV view"
                      : page === "video-test"
                        ? "Local recording test · Timestamps are offsets within the video · No live monitoring"
                        : pilot
                          ? "Local pharmacy records · Access restricted to the active branch · All displayed times Europe/Dublin"
                          : "Pharmacy-only prototype · Synthetic records · All displayed times Europe/Dublin"}
                  </span>
                  <span className="footnote-version">AisleSignals 0.1</span>
                </div>
              </>
            )}
          </main>
        </div>
        {toast && (
          <div className="toast" role="status">
            <Check size={18} />
            {toast}
          </div>
        )}
        {modal && data && (modal !== "simulator" || !pilot) && (
          <Modal
            title={
              modal === "manual"
                ? "Create a manual case"
                : modal === "assistance"
                  ? "Ask for team assistance"
                  : "Scenario simulator"
            }
            onClose={closeModal}
          >
            {modal === "manual" ? (
              <ManualForm
                siteId={data.site.id}
                disabled={disabled}
                act={act}
                done={(id) => {
                  setModal(null);
                  openIncident(id);
                }}
              />
            ) : modal === "assistance" ? (
              <AssistanceForm
                siteId={data.site.id}
                disabled={disabled}
                act={act}
                done={() => {
                  setModal(null);
                  navigate("assistance");
                }}
              />
            ) : (
              <Simulator
                disabled={disabled}
                act={act}
                active={data.site.shift_active}
                done={(id) => {
                  setModal(null);
                  if (id) openCandidate(id);
                }}
              />
            )}
          </Modal>
        )}
      </div>
    </PilotContext.Provider>
  );
}

function Overview({
  data,
  manager,
  disabled,
  act,
  navigate,
  openCandidate,
  openIncident,
  simulator,
}: {
  data: Bootstrap;
  manager: boolean;
  disabled: boolean;
  act: Act;
  navigate: (page: Page) => void;
  openCandidate: (id: string) => void;
  openIncident: (id: string) => void;
  simulator: () => void;
}) {
  const pilot = data.mode === "pilot";
  const open = data.incidents.filter((i) => i.status === "OPEN");
  const waiting = data.candidates.filter(
    (c) => c.status === "NEW" || c.status === "ACKNOWLEDGED",
  );
  const help = data.assistance.filter((a) => a.status !== "RESOLVED");
  const ready = data.cameras.filter((c) => c.status === "DEMO_ONLINE").length;
  return (
    <>
      <div className="overview-banner">
        <div className="banner-symbol">
          <Radio size={25} />
        </div>
        <div>
          <div className="banner-title">
            {pilot
              ? "Start with your pharmacy’s camera view."
              : "Your demonstration workspace is ready."}
          </div>
          <p>
            {pilot
              ? "Open LIVE DETECTION, select the authorised CCTV view and check the camera area. Product-interaction results and review history are kept with that session’s branch. This workspace does not monitor in the background."
              : "Walk through a shelf observation, review the facts and record an outcome. Every scenario is synthetic."}
          </p>
        </div>
        {(pilot || manager) && (
          <button
            className="button banner-button"
            disabled={disabled}
            onClick={pilot ? () => navigate("live-detection") : simulator}
          >
            {pilot ? "Open LIVE DETECTION" : "Run a scenario"}
            <ArrowRight size={16} />
          </button>
        )}
      </div>
      <div className="stats-grid">
        <Stat
          name="Needs attention"
          value={getAlertCount(data.candidates)}
          detail="New, non-historical observations"
          icon={Radio}
          onClick={() => navigate("review")}
          tone="amber"
        />
        <Stat
          name="Open cases"
          value={open.length}
          detail="Being reviewed by your team"
          icon={ClipboardList}
          onClick={() => navigate("incidents")}
        />
        <Stat
          name="Team requests"
          value={help.length}
          detail="Waiting for acknowledgement or resolution"
          icon={Bell}
          onClick={() => navigate("assistance")}
        />
        <Stat
          name={pilot ? "Camera readiness" : "Simulated sources ready"}
          value={pilot ? "Check locally" : `${ready}/${data.cameras.length}`}
          detail={
            pilot
              ? "Verify the selected view before each session"
              : "Demo status, not camera coverage"
          }
          icon={Video}
          onClick={() => navigate("cameras")}
        />
      </div>
      <div className="overview-grid">
        <Panel
          title="Ready for your review"
          description="Start with the facts. Decide what happens next."
          action={
            <button className="text-button" onClick={() => navigate("review")}>
              View queue
              <ArrowRight size={15} />
            </button>
          }
        >
          {waiting.length ? (
            waiting
              .slice(0, 4)
              .map((c) => (
                <CandidateRow
                  key={c.id}
                  candidate={c}
                  onClick={() => openCandidate(c.id)}
                />
              ))
          ) : (
            <Empty
              title="The queue is clear"
              detail={
                pilot
                  ? "Product-interaction results appear in LIVE DETECTION. Use the casebook to record reviewed incidents."
                  : "New synthetic observations will appear here when you run a scenario."
              }
            />
          )}
          <div className="panel-bottom">
            <ShieldCheck size={15} />
            <span>
              No automatic conclusions about a person or their intent.
            </span>
          </div>
        </Panel>
        <Panel
          title="Branch at a glance"
          description="The practical details for this session."
        >
          <div className="glance-list">
            <div>
              <span>Workflow shift</span>
              <Badge value={data.site.shift_active ? "OPEN" : "CLOSED"}>
                {data.site.shift_active
                  ? pilot
                    ? "Active"
                    : "Active · demo"
                  : pilot
                    ? "Paused"
                    : "Paused · demo"}
              </Badge>
            </div>
            <div>
              <span>Recorded loss</span>
              <strong>
                {money(
                  data.incidents.reduce(
                    (sum, item) => sum + (item.loss_cents ?? 0),
                    0,
                  ),
                )}
              </strong>
            </div>
            <div>
              <span>Recorded recovery</span>
              <strong>
                {money(
                  data.incidents.reduce(
                    (sum, item) => sum + (item.recovered_cents ?? 0),
                    0,
                  ),
                )}
              </strong>
            </div>
            <div>
              <span>Cloud AI spend</span>
              <strong>{money(data.budget.spent_cents)}</strong>
            </div>
            <div>
              <span>Branch subscription</span>
              <strong>
                €60 <small>/ month</small>
              </strong>
            </div>
          </div>
          <p className="card-note">
            {pilot
              ? "Amounts reflect staff-entered records. Recovery is recorded separately and is not a claim of savings."
              : "Amounts are synthetic recorded values. Recovery is recorded separately and is not a claim of savings."}
          </p>
          <button
            className="button secondary full"
            disabled={disabled}
            onClick={() =>
              void act(
                "/shift",
                { active: !data.site.shift_active },
                {
                  success: data.site.shift_active
                    ? pilot
                      ? "Workflow paused."
                      : "Demo workflow paused."
                    : pilot
                      ? "Workflow activated."
                      : "Demo workflow activated.",
                },
              )
            }
          >
            {data.site.shift_active ? <Pause size={16} /> : <Play size={16} />}{" "}
            {data.site.shift_active
              ? pilot
                ? "Pause workflow shift"
                : "Pause demo shift"
              : pilot
                ? "Activate workflow shift"
                : "Activate demo shift"}
          </button>
        </Panel>
        <Panel
          title="Recent casework"
          description="Reviewed observations and manually recorded cases."
          action={
            <button
              className="text-button"
              onClick={() => navigate("incidents")}
            >
              Open casebook
              <ArrowRight size={15} />
            </button>
          }
        >
          {data.incidents.length ? (
            <div className="case-preview-list">
              {data.incidents.slice(0, 3).map((i) => (
                <button
                  key={i.id}
                  className="case-preview"
                  onClick={() => openIncident(i.id)}
                >
                  <div className="case-icon">
                    <FileText size={19} />
                  </div>
                  <div>
                    <span className="reference">{i.reference}</span>
                    <strong>{i.title}</strong>
                    <span>{date(i.updated_at)}</span>
                  </div>
                  <Badge value={i.status} />
                  <ChevronRight size={17} />
                </button>
              ))}
            </div>
          ) : (
            <Empty
              title="A clean casebook"
              detail="Open a case from a reviewed observation or add a manual record."
            />
          )}
        </Panel>
        <section className="quiet-card">
          <span className="eyebrow">THE AISLESIGNALS APPROACH</span>
          <h2>
            A signal starts a review.
            <br />A person makes the call.
          </h2>
          <p>
            Keep context, record uncertainty and follow up calmly. Clear
            information helps your team decide.
          </p>
          <span className="quiet-signature">
            <Mark />
            Thoughtful by design.
          </span>
        </section>
      </div>
    </>
  );
}
function Stat({
  name,
  value,
  detail,
  icon: Icon,
  onClick,
  tone = "teal",
}: {
  name: string;
  value: string | number;
  detail: string;
  icon: typeof Activity;
  onClick: () => void;
  tone?: string;
}) {
  return (
    <button className="stat-card" onClick={onClick}>
      <div className="stat-top">
        <span>{name}</span>
        <div className={`stat-icon ${tone}`}>
          <Icon size={19} />
        </div>
      </div>
      <strong>{value}</strong>
      <div className="stat-detail">
        {detail}
        <ArrowRight size={14} />
      </div>
    </button>
  );
}
function CandidateRow({
  candidate: c,
  onClick,
  selected = false,
}: {
  candidate: Candidate;
  onClick: () => void;
  selected?: boolean;
}) {
  return (
    <button
      className={`candidate-row ${selected ? "selected" : ""}`}
      onClick={onClick}
    >
      <div
        className={`event-icon ${c.media_status === "MISSING" ? "missing" : ""}`}
      >
        <Video size={20} />
      </div>
      <div className="candidate-row-main">
        <div className="row-title">{c.title}</div>
        <div className="row-meta">
          {c.camera_name}
          <span>·</span>
          {c.zone}
        </div>
        <div className="row-labels">
          <span className="source-tag">Synthetic</span>
          {c.historical && (
            <span className="source-tag historical">Historical</span>
          )}
          {c.media_status === "MISSING" && (
            <span className="source-tag missing">Media unavailable</span>
          )}
        </div>
      </div>
      <div className="candidate-row-end">
        <Badge value={c.status} />
        <time>{date(c.occurred_at)}</time>
      </div>
      <ChevronRight className="row-chevron" size={17} />
    </button>
  );
}

function Review({
  data,
  selectedId,
  select,
  openIncident,
  disabled,
  act,
  manager,
  simulator,
}: {
  data: Bootstrap;
  selectedId: string | null;
  select: (id: string | null) => void;
  openIncident: (id: string) => void;
  disabled: boolean;
  act: Act;
  manager: boolean;
  simulator: () => void;
}) {
  const pilot = data.mode === "pilot";
  const [filter, setFilter] = useState("ACTIVE");
  const [query, setQuery] = useState("");
  const filtered = data.candidates.filter(
    (c) =>
      (filter === "ALL" ||
        (filter === "ACTIVE" &&
          (c.status === "NEW" || c.status === "ACKNOWLEDGED")) ||
        c.status === filter) &&
      `${c.title} ${c.camera_name} ${c.zone}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const selected = data.candidates.find((c) => c.id === selectedId);
  return (
    <>
      <div className="toolbar">
        <div className="segmented" aria-label="Observation filter">
          {[
            ["ACTIVE", "To review"],
            ["ALL", "All observations"],
            ["CONVERTED", "Case opened"],
            ["DISMISSED", "Dismissed"],
          ].map(([value, title]) => (
            <button
              key={value}
              aria-pressed={filter === value}
              className={filter === value ? "active" : ""}
              onClick={() => setFilter(value)}
            >
              {title}
            </button>
          ))}
        </div>
        <label className="search-field">
          <Search size={17} />
          <input
            aria-label="Search observations"
            placeholder="Search observations"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        {manager && !pilot && (
          <button
            className="button secondary"
            disabled={disabled}
            onClick={simulator}
          >
            <Play size={15} />
            Run a scenario
          </button>
        )}
      </div>
      <div className={`review-layout ${selected ? "has-detail" : ""}`}>
        <section className="panel queue-panel">
          <div className="queue-title">
            <strong>
              {filtered.length}{" "}
              {filtered.length === 1 ? "observation" : "observations"}
            </strong>
            <span>Latest 200 records</span>
          </div>
          {filtered.length ? (
            filtered.map((c) => (
              <CandidateRow
                candidate={c}
                selected={selectedId === c.id}
                key={c.id}
                onClick={() => select(c.id)}
              />
            ))
          ) : (
            <Empty
              title="Nothing in this view"
              detail={
                pilot
                  ? "Change the filter or open LIVE DETECTION to review saved product-interaction results."
                  : "Change the filter, clear your search or run a synthetic scenario."
              }
            />
          )}
        </section>
        {selected ? (
          <CandidateDetail
            key={selected.id}
            candidate={selected}
            disabled={disabled}
            act={act}
            close={() => select(null)}
            openIncident={openIncident}
          />
        ) : (
          <section className="review-placeholder">
            <div className="large-outline">
              <Radio size={34} />
            </div>
            <h2>A little context goes a long way.</h2>
            <p>
              {pilot
                ? "Select an observation to review its source and timing before deciding what to do. Product-interaction results are reviewed within LIVE DETECTION."
                : "Select an observation to review its source, timing and synthetic evidence before deciding what to do."}
            </p>
            <div>
              <span>01 Review the context</span>
              <span>02 Record your reasoning</span>
              <span>03 Decide the next step</span>
            </div>
          </section>
        )}
      </div>
    </>
  );
}
function CandidateDetail({
  candidate: c,
  disabled,
  act,
  close,
  openIncident,
}: {
  candidate: Candidate;
  disabled: boolean;
  act: Act;
  close: () => void;
  openIncident: (id: string) => void;
}) {
  const [reason, setReason] = useDraft(`candidate:${c.id}`, "");
  const [version, setVersion] = useState(c.version);
  const [imageFailed, setImageFailed] = useState(false);
  const [imageAttempt, setImageAttempt] = useState(0);
  const terminal = c.status === "DISMISSED" || c.status === "CONVERTED";
  const evidence = safeEvidenceUrl(c.evidence_url);
  async function review(decision: "DISMISS" | "OPEN_INCIDENT") {
    const result = await act<Candidate>(
      `/candidates/${c.id}/review`,
      { expected_version: version, decision, reason },
      {
        success:
          decision === "DISMISS"
            ? "Observation dismissed with your review reason."
            : "Case opened for human review.",
      },
    );
    if (result) {
      setReason("");
      setVersion(result.version);
      if (result.incident_id) openIncident(result.incident_id);
    }
  }
  return (
    <section className="panel candidate-detail">
      <div className="detail-heading">
        <div>
          <span className="eyebrow">OBSERVATION DETAILS</span>
          <h2>{c.title}</h2>
        </div>
        <button
          className="icon-button"
          aria-label="Close observation"
          onClick={close}
        >
          <X size={19} />
        </button>
      </div>
      <div className="detail-tags">
        <Badge value={c.status} />
        <span className="source-tag">Synthetic source</span>
        {c.historical && (
          <span className="source-tag historical">
            Historical · no live alert
          </span>
        )}
      </div>
      <div className="evidence-frame">
        {evidence && !imageFailed ? (
          <img
            key={imageAttempt}
            src={evidence}
            alt={`Synthetic illustration for ${c.title}. This is not real camera footage.`}
            onError={() => setImageFailed(true)}
          />
        ) : (
          <div className="missing-evidence">
            <Video size={31} />
            <strong>Evidence unavailable</strong>
            <span>
              {c.media_status === "MISSING"
                ? "This scenario deliberately has no media."
                : "This illustration could not be loaded. Refresh after reconnecting."}
            </span>
            {c.media_status === "AVAILABLE" && (
              <button
                className="button secondary"
                onClick={() => {
                  setImageFailed(false);
                  setImageAttempt((a) => a + 1);
                }}
              >
                Retry illustration
              </button>
            )}
          </div>
        )}
        <span className="evidence-watermark">
          SIMULATED ILLUSTRATION · NO REAL FOOTAGE
        </span>
      </div>
      <div className="detail-body">
        <dl className="fact-grid">
          <div>
            <dt>Source</dt>
            <dd>{c.camera_name}</dd>
          </div>
          <div>
            <dt>Zone</dt>
            <dd>{c.zone}</dd>
          </div>
          <div>
            <dt>Occurred</dt>
            <dd>{date(c.occurred_at, true)}</dd>
          </div>
          <div>
            <dt>Received</dt>
            <dd>{date(c.received_at, true)}</dd>
          </div>
        </dl>
        <div className="observation-summary">
          <h3>Observed context</h3>
          <p>{c.summary}</p>
        </div>
        <div className="notice">
          <CircleHelp size={16} />
          <span>
            This is an observation, not a finding of theft. Keep uncertainty and
            context in your review.
          </span>
        </div>
        {!terminal && version !== c.version && (
          <div className="notice amber">
            This observation changed since you opened it.{" "}
            <button
              className="text-button"
              onClick={() => setVersion(c.version)}
            >
              Use latest version (keep reason)
            </button>
          </div>
        )}
        {terminal ? (
          <div className="terminal-action">
            <p>
              {c.status === "DISMISSED"
                ? "A reviewer dismissed this observation. The decision is recorded in activity."
                : "This observation is linked to a case for follow-up."}
            </p>
            {c.incident_id && (
              <button
                className="button primary full"
                onClick={() => openIncident(c.incident_id!)}
              >
                Open linked case
                <ArrowRight size={17} />
              </button>
            )}
          </div>
        ) : (
          <>
            <label className="review-reason">
              Your review reason
              <textarea
                placeholder="Record what you can establish, what is uncertain and why this action is appropriate…"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                minLength={5}
                maxLength={2000}
                rows={4}
              />
              <span className="field-hint">
                Required for a decision · at least 5 characters
              </span>
            </label>
            <div className="review-actions">
              <button
                className="button secondary"
                disabled={
                  disabled || reason.trim().length < 5 || version !== c.version
                }
                onClick={() => void review("DISMISS")}
              >
                Dismiss observation
              </button>
              <button
                className="button primary"
                disabled={
                  disabled || reason.trim().length < 5 || version !== c.version
                }
                onClick={() => void review("OPEN_INCIDENT")}
              >
                Open a case
                <ArrowRight size={16} />
              </button>
            </div>
            {c.status === "NEW" && (
              <button
                className="text-button acknowledgement"
                disabled={disabled}
                onClick={async () => {
                  const result = await act<Candidate>(
                    `/candidates/${c.id}/acknowledge`,
                    { expected_version: version },
                    {
                      success:
                        "Observation acknowledged. A review decision is still needed.",
                    },
                  );
                  if (result) setVersion(result.version);
                }}
              >
                <Check size={15} />
                Acknowledge without a decision
              </button>
            )}
          </>
        )}
      </div>
    </section>
  );
}

function Casebook({
  data,
  selectedId,
  select,
  disabled,
  act,
  manager,
  openCandidate,
  create,
}: {
  data: Bootstrap;
  selectedId: string | null;
  select: (id: string | null) => void;
  disabled: boolean;
  act: Act;
  manager: boolean;
  openCandidate: (id: string) => void;
  create: () => void;
}) {
  const pilot = data.mode === "pilot";
  const [filter, setFilter] = useState("ALL");
  const [query, setQuery] = useState("");
  const selected = data.incidents.find((i) => i.id === selectedId);
  const filtered = data.incidents.filter(
    (i) =>
      (filter === "ALL" || i.status === filter) &&
      `${i.reference} ${i.title}`.toLowerCase().includes(query.toLowerCase()),
  );
  if (selected)
    return (
      <CaseDetail
        key={selected.id}
        incident={selected}
        manager={manager}
        disabled={disabled}
        act={act}
        back={() => select(null)}
        openCandidate={openCandidate}
      />
    );
  return (
    <>
      <div className="toolbar">
        <div className="segmented" aria-label="Case filter">
          {["ALL", "OPEN", "CLOSED"].map((v) => (
            <button
              key={v}
              aria-pressed={filter === v}
              className={filter === v ? "active" : ""}
              onClick={() => setFilter(v)}
            >
              {v === "ALL" ? "All cases" : label(v)}
            </button>
          ))}
        </div>
        <label className="search-field">
          <Search size={17} />
          <input
            aria-label="Search cases"
            placeholder="Search reference or title"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
      </div>
      <div className="panel">
        {filtered.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Case</th>
                  <th>Classification</th>
                  <th>Status</th>
                  <th>Updated</th>
                  <th>
                    <span className="sr-only">Open</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((i) => (
                  <tr key={i.id}>
                    <td>
                      <button
                        className="table-case-link"
                        onClick={() => select(i.id)}
                      >
                        <span className="reference">{i.reference}</span>
                        <strong>{i.title}</strong>
                        <span className="source-tag">
                          {pilot ? "Staff record" : "Synthetic record"}
                        </span>
                      </button>
                    </td>
                    <td>{label(i.classification)}</td>
                    <td>
                      <Badge value={i.status} />
                    </td>
                    <td className="muted">{date(i.updated_at)}</td>
                    <td>
                      <button
                        className="icon-button"
                        aria-label={`Open case ${i.reference}`}
                        onClick={() => select(i.id)}
                      >
                        <ArrowRight size={17} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title="No cases in this view"
            detail="Review an observation or record an incident manually to begin."
          >
            <button
              className="button secondary"
              disabled={disabled}
              onClick={create}
            >
              <Plus size={16} />
              New manual case
            </button>
          </Empty>
        )}
      </div>
    </>
  );
}
function CaseInteractionSource({ incident }: { incident: Incident }) {
  const [evidence, setEvidence] = useState<{
    source: InteractionCaseSource;
    evidence_status: string;
    frames: { at_seconds: number; url: string }[];
  } | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [unavailableFrames, setUnavailableFrames] = useState<number[]>([]);
  useEffect(() => {
    let active = true;
    setEvidence(null);
    setError("");
    setUnavailableFrames([]);
    void api<typeof evidence>(`/incidents/${incident.id}/interaction-source`)
      .then((result) => {
        if (active) setEvidence(result);
      })
      .catch((failure) => {
        if (active)
          setError(
            failure instanceof Error
              ? failure.message
              : "Unable to load the linked evidence.",
          );
      });
    return () => {
      active = false;
    };
  }, [incident.id, revision]);
  const source = incident.interaction_source!;
  return (
    <section
      className="interaction-case-source"
      aria-label="Linked product observation"
    >
      <h3>Linked product observation</h3>
      <p>
        {source.source_kind === "RECORDED_VIDEO"
          ? "Recorded-video test"
          : "Browser-provided CCTV source"}{" "}
        · {source.source_label}
      </p>
      <CameraContextDetails context={source.camera_context} />
      <p>
        <strong>Unverified model context:</strong>{" "}
        {label(source.observation.action)}. {source.observation.reason}
      </p>
      <p>
        Staff review at linking: {label(source.review.outcome)} ·{" "}
        {source.review.by} · {date(source.review.at)}
      </p>
      {source.review.note && <p>{source.review.note}</p>}
      <p>
        Linked by {source.linked_by.name} at {date(source.linked_at)}.
        Observation version {source.version} · {source.observation.model}
        {source.observation.prompt_version
          ? ` · prompt ${source.observation.prompt_version}`
          : ""}
        .
      </p>
      <p>{source.retention_notice}</p>
      <p role="status">
        {error ||
          (!evidence
            ? "Checking source evidence…"
            : evidence.evidence_status === "available"
              ? `Sampled JPEGs available until ${date(source.expires_at)}.`
              : `Sampled JPEGs ${evidence.evidence_status}. The case retains the source metadata and hashes; no images were preserved beyond their original policy.`)}
      </p>
      <button type="button" onClick={() => setRevision((value) => value + 1)}>
        Refresh linked evidence
      </button>
      <div className="interaction-frames">
        {evidence?.frames.map((frame, index) => {
          const url = safeInteractionFrameUrl(frame.url, source.id);
          return (
            <figure key={`${revision}:${index}`}>
              {url && !unavailableFrames.includes(index) ? (
                <img
                  src={url}
                  alt={`Linked source frame ${index + 1} at ${frame.at_seconds.toFixed(2)} seconds`}
                  onError={() =>
                    setUnavailableFrames((previous) => [...previous, index])
                  }
                />
              ) : (
                <p>
                  Frame unavailable or expired. Refresh the evidence status.
                </p>
              )}
              <figcaption>
                Frame {index + 1} · {frame.at_seconds.toFixed(2)}s
              </figcaption>
            </figure>
          );
        })}
      </div>
      <details>
        <summary>Source identifiers and evidence hashes</summary>
        <p>
          Observation {source.id} · Run {source.run_id}
        </p>
        {source.frames.map((frame, index) => (
          <p key={index}>
            Frame {index + 1} · {frame.at_seconds.toFixed(2)}s · SHA-256{" "}
            {frame.sha256}
          </p>
        ))}
      </details>
    </section>
  );
}
function CaseDetail({
  incident: i,
  manager,
  disabled,
  act,
  back,
  openCandidate,
}: {
  incident: Incident;
  manager: boolean;
  disabled: boolean;
  act: Act;
  back: () => void;
  openCandidate: (id: string) => void;
}) {
  const pilot = useContext(PilotContext);
  const [form, setForm] = useDraft(`case:${i.id}`, caseFields(i));
  const [version, setVersion] = useState(i.version);
  const [validation, setValidation] = useState("");
  const [modal, setModal] = useState<"close" | "reopen" | "export" | null>(
    null,
  );
  const [task, setTask] = useState({ title: "", assignee: "", due: "" });
  const closed = i.status === "CLOSED";
  const changedVersion = version !== i.version;
  const dirty = JSON.stringify(form) !== JSON.stringify(caseFields(i));
  const blocked = disabled || closed || changedVersion;
  const closeModal = useCallback(() => setModal(null), []);
  useEffect(() => {
    if (!dirty) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);
  function field<K extends keyof CaseForm>(key: K, value: CaseForm[K]) {
    setForm((f) => ({ ...f, [key]: value }));
    setValidation("");
  }
  function adopt(result: Incident) {
    setVersion(result.version);
    setForm(caseFields(result));
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    setValidation("");
    try {
      const payload = buildCasePatch(i, form, version, manager);
      const result = await act<Incident>(`/incidents/${i.id}`, payload, {
        method: "PATCH",
        success:
          "Case details saved. Any older report approval has been invalidated.",
      });
      if (result) adopt(result);
    } catch (err) {
      setValidation(
        err instanceof Error ? err.message : "Check the case fields.",
      );
    }
  }
  async function addTask(event: FormEvent) {
    event.preventDefault();
    setValidation("");
    if (!task.due || Number.isNaN(new Date(task.due).valueOf())) {
      setValidation("Choose a valid task due date and time.");
      return;
    }
    const result = await act<Incident>(
      `/incidents/${i.id}/tasks`,
      {
        expected_version: version,
        title: task.title,
        assignee: task.assignee,
        due_at: new Date(task.due).toISOString(),
      },
      { success: "Follow-up task added." },
    );
    if (result) {
      setVersion(result.version);
      setTask({ title: "", assignee: "", due: "" });
    }
  }
  return (
    <>
      <div className="case-topline">
        <button
          className="text-button"
          onClick={() => {
            if (
              !dirty ||
              window.confirm("Leave this case and discard unsaved edits?")
            )
              back();
          }}
        >
          <ArrowLeft size={17} />
          Back to casebook
        </button>
        <span className="source-tag">
          {pilot ? "Case" : "Synthetic case"} · {i.reference}
        </span>
        <Badge value={i.status} />
      </div>
      <div className="case-title-row">
        <div>
          <h2>{i.title}</h2>
          <p>
            Created {date(i.created_at, true)} · Version {i.version}
          </p>
        </div>
        <div className="page-actions">
          {manager && (
            <button
              className="button secondary"
              disabled={disabled || dirty || changedVersion}
              onClick={() => setModal("export")}
            >
              <ArrowDownToLine size={16} />
              Export case JSON
            </button>
          )}
          {closed ? (
            manager && (
              <button
                className="button primary"
                disabled={disabled || changedVersion}
                onClick={() => setModal("reopen")}
              >
                Reopen case
              </button>
            )
          ) : (
            <button
              className="button primary"
              disabled={
                disabled ||
                dirty ||
                changedVersion ||
                i.outcome === "UNRESOLVED" ||
                i.tasks.some((t) => !t.done)
              }
              onClick={() => setModal("close")}
            >
              <CheckCheck size={17} />
              Close case
            </button>
          )}
        </div>
      </div>
      {changedVersion && (
        <div className="notice amber">
          <CircleHelp size={19} />
          <span>
            A newer version is available. Your unsaved edits are still here.
            Review the latest record before continuing.
          </span>
          <button
            className="text-button"
            onClick={() => {
              if (
                !dirty ||
                window.confirm(
                  "Load the latest record and replace your unsaved edits?",
                )
              )
                adopt(i);
            }}
          >
            Load latest record
          </button>
        </div>
      )}
      {dirty && (
        <div className="notice">
          Unsaved case edits. Save these before generating a report, exporting
          or closing.
        </div>
      )}
      {closed && (
        <div className="notice">
          This case is closed. A manager must reopen it before any changes can
          be made.
        </div>
      )}
      {validation && (
        <div className="inline-error" role="alert">
          {validation}
        </div>
      )}
      <div className="case-layout">
        <div className="case-main">
          {i.interaction_source && (
            <CaseInteractionSource key={i.id} incident={i} />
          )}
          <Panel
            title="The reviewed facts"
            description="Separate what is established from what still needs review."
            action={
              i.candidate_id ? (
                <button
                  className="text-button"
                  onClick={() => {
                    if (
                      !dirty ||
                      window.confirm(
                        "Leave this case and discard unsaved edits?",
                      )
                    )
                      openCandidate(i.candidate_id!);
                  }}
                >
                  Source observation
                  <ArrowRight size={15} />
                </button>
              ) : (
                <span className="source-tag">
                  {i.interaction_source
                    ? "Reviewed product observation"
                    : "Manual record"}
                </span>
              )
            }
          >
            <form onSubmit={save} className="panel-form">
              <fieldset disabled={blocked}>
                <label>
                  Case title
                  <input
                    required
                    minLength={3}
                    maxLength={120}
                    value={form.title}
                    onChange={(e) => field("title", e.target.value)}
                  />
                </label>
                <label>
                  Reviewed notes
                  <textarea
                    required
                    minLength={5}
                    maxLength={4000}
                    rows={6}
                    value={form.notes}
                    onChange={(e) => field("notes", e.target.value)}
                  />
                  <span className="field-hint">
                    {pilot
                      ? "Record reviewed facts only."
                      : "Use synthetic facts only."}{" "}
                    Do not enter patient data or identifying allegations.
                  </span>
                </label>
                <div className="form-columns">
                  <label>
                    Classification
                    <select
                      value={form.classification}
                      onChange={(e) =>
                        field(
                          "classification",
                          e.target.value as Classification,
                        )
                      }
                      disabled={
                        !manager && i.classification === "STORE_CONFIRMED_LOSS"
                      }
                    >
                      {(
                        [
                          "UNASSESSED",
                          "BENIGN",
                          "INSUFFICIENT_EVIDENCE",
                          "SUSPECTED_INCIDENT",
                          "STORE_CONFIRMED_LOSS",
                        ] as Classification[]
                      )
                        .filter(
                          (v) =>
                            manager ||
                            v !== "STORE_CONFIRMED_LOSS" ||
                            i.classification === v,
                        )
                        .map((v) => (
                          <option key={v} value={v}>
                            {label(v)}
                          </option>
                        ))}
                    </select>
                  </label>
                  <label>
                    Outcome
                    <select
                      value={form.outcome}
                      onChange={(e) =>
                        field("outcome", e.target.value as Outcome)
                      }
                    >
                      {(
                        [
                          "UNRESOLVED",
                          "NO_LOSS_ESTABLISHED",
                          "GOODS_RETURNED",
                          "GOODS_PAID_FOR",
                          "LOSS_RECORDED",
                        ] as Outcome[]
                      )
                        .filter(
                          (v) =>
                            manager || v !== "LOSS_RECORDED" || i.outcome === v,
                        )
                        .map((v) => (
                          <option key={v} value={v}>
                            {label(v)}
                          </option>
                        ))}
                    </select>
                  </label>
                </div>
                {manager ? (
                  <>
                    <div className="form-columns">
                      <label>
                        Recorded loss (€)
                        <input
                          inputMode="decimal"
                          placeholder="Unknown"
                          value={form.loss}
                          onChange={(e) => field("loss", e.target.value)}
                        />
                      </label>
                      <label>
                        Recorded recovery (€)
                        <input
                          inputMode="decimal"
                          placeholder="Unknown"
                          value={form.recovered}
                          onChange={(e) => field("recovered", e.target.value)}
                        />
                      </label>
                    </div>
                    <p className="field-hint">
                      Blank means unknown; zero means established as €0.
                      Financial loss requires “Store confirmed loss” and “Loss
                      recorded”. Benign or no-loss outcomes must clear both
                      amounts. Recovery cannot exceed loss.
                    </p>
                  </>
                ) : (
                  <div className="financial-readout">
                    <span>
                      Recorded loss <strong>{money(i.loss_cents)}</strong>
                    </span>
                    <span>
                      Recorded recovery{" "}
                      <strong>{money(i.recovered_cents)}</strong>
                    </span>
                    <p>
                      Only a manager may confirm loss or edit monetary values.
                    </p>
                  </div>
                )}
                <div className="form-footer">
                  <span>
                    {dirty
                      ? "You have unsaved changes."
                      : "All case details saved."}
                  </span>
                  <button
                    className="button primary"
                    disabled={blocked || !dirty}
                  >
                    Save case details
                    <Check size={16} />
                  </button>
                </div>
              </fieldset>
            </form>
          </Panel>
          <Panel
            title="Follow-up tasks"
            description="A case can close once every task is complete and an outcome is recorded."
          >
            {i.tasks.length ? (
              <div className="task-list">
                {i.tasks.map((t) => (
                  <div
                    key={t.id}
                    className={`task-row ${t.done ? "done" : ""}`}
                  >
                    <button
                      className="task-checkbox"
                      aria-label={`Complete task: ${t.title}`}
                      disabled={blocked || t.done || dirty}
                      onClick={async () => {
                        const result = await act<Incident>(
                          `/incidents/${i.id}/tasks/${t.id}/complete`,
                          { expected_version: version },
                          { success: "Task marked complete." },
                        );
                        if (result) setVersion(result.version);
                      }}
                    >
                      {t.done && <Check size={15} />}
                    </button>
                    <div>
                      <strong>{t.title}</strong>
                      <span>
                        {t.assignee} · Due {date(t.due_at)}
                      </span>
                    </div>
                    {t.done && <Badge value="CLOSED">Done</Badge>}
                  </div>
                ))}
              </div>
            ) : (
              <p className="section-empty">No follow-up tasks recorded.</p>
            )}
            {!closed && (
              <form className="task-form" onSubmit={addTask}>
                <fieldset disabled={blocked || dirty}>
                  <label>
                    Task title
                    <input
                      required
                      minLength={3}
                      maxLength={200}
                      placeholder="e.g. Review the stock count"
                      value={task.title}
                      onChange={(e) =>
                        setTask({ ...task, title: e.target.value })
                      }
                    />
                  </label>
                  <div className="form-columns">
                    <label>
                      Assigned colleague
                      <input
                        required
                        minLength={2}
                        maxLength={100}
                        placeholder="Colleague name"
                        value={task.assignee}
                        onChange={(e) =>
                          setTask({ ...task, assignee: e.target.value })
                        }
                      />
                    </label>
                    <label>
                      Due date (your device time)
                      <input
                        required
                        type="datetime-local"
                        value={task.due}
                        onChange={(e) =>
                          setTask({ ...task, due: e.target.value })
                        }
                      />
                    </label>
                  </div>
                  <button className="button secondary">
                    <Plus size={16} />
                    Add follow-up task
                  </button>
                </fieldset>
              </form>
            )}
          </Panel>
        </div>
        <div className="case-aside">
          <Panel
            title="Draft the record"
            description="A local factual template. No AI inference or paid API calls."
          >
            <div className="draft-body">
              <div className="template-label">
                <FileText size={16} />
                <span>LOCAL TEMPLATE · €0</span>
              </div>
              {i.draft ? (
                <>
                  <pre className="report-draft">{i.draft.text}</pre>
                  <Badge value={i.draft.approved ? "CLOSED" : "ACKNOWLEDGED"}>
                    {i.draft.approved
                      ? "Reviewed and approved"
                      : "Staff review required"}
                  </Badge>
                  {!i.draft.approved && (
                    <button
                      className="button primary full"
                      disabled={disabled || changedVersion || dirty}
                      onClick={async () => {
                        const result = await act<Incident>(
                          `/incidents/${i.id}/draft/approve`,
                          { expected_version: version },
                          {
                            success:
                              "Template report approved by a staff reviewer.",
                          },
                        );
                        if (result) setVersion(result.version);
                      }}
                    >
                      <Check size={16} />
                      Approve reviewed draft
                    </button>
                  )}
                </>
              ) : (
                <p className="draft-empty">
                  Turn the current structured facts into a consistent report.
                  Review the draft before approving it.
                </p>
              )}
              <button
                className="button secondary full"
                disabled={disabled || changedVersion || dirty}
                onClick={async () => {
                  const result = await act<Incident>(
                    `/incidents/${i.id}/draft`,
                    { expected_version: version },
                    {
                      success:
                        "Local factual template generated. Review before approval.",
                    },
                  );
                  if (result) setVersion(result.version);
                }}
              >
                <FileText size={16} />
                {i.draft
                  ? "Regenerate local template"
                  : "Generate local template"}
              </button>
              <p className="field-hint">
                Changing reviewed facts invalidates any previous draft approval.
              </p>
            </div>
          </Panel>
          <Panel
            title="Case timeline"
            description="Human actions and record changes."
          >
            <div className="timeline">
              {i.history.length ? (
                i.history
                  .slice()
                  .reverse()
                  .map((h) => (
                    <div key={h.id} className="timeline-item">
                      <span className="timeline-dot" />
                      <div>
                        <strong>{label(h.action)}</strong>
                        <p>{h.detail}</p>
                        <span>
                          {h.actor} · {date(h.at)}
                        </span>
                      </div>
                    </div>
                  ))
              ) : (
                <p>No history recorded yet.</p>
              )}
            </div>
          </Panel>
        </div>
      </div>
      {!closed && (
        <p className="closure-note">
          <ShieldCheck size={16} />
          {i.outcome === "UNRESOLVED"
            ? "Choose a resolved outcome before closing this case."
            : i.tasks.some((t) => !t.done)
              ? "Complete the remaining follow-up tasks before closing this case."
              : "This case can close after saved changes and a closure reason."}
        </p>
      )}
      {modal && (
        <Modal
          title={
            modal === "close"
              ? "Close this case"
              : modal === "reopen"
                ? "Reopen this case"
                : pilot
                  ? "Export case record"
                  : "Export synthetic case record"
          }
          onClose={closeModal}
        >
          <CaseActionForm
            kind={modal}
            incident={i}
            version={version}
            disabled={disabled || changedVersion}
            act={act}
            done={(result) => {
              if (result) adopt(result);
              setModal(null);
            }}
          />
        </Modal>
      )}
    </>
  );
}
function CaseActionForm({
  kind,
  incident,
  version,
  disabled,
  act,
  done,
}: {
  kind: "close" | "reopen" | "export";
  incident: Incident;
  version: number;
  disabled: boolean;
  act: Act;
  done: (incident: Incident | null) => void;
}) {
  const pilot = useContext(PilotContext);
  const [reason, setReason] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (kind === "export") {
      const blob = await act<Blob>(
        `/incidents/${incident.id}/export`,
        { expected_version: version, purpose: reason },
        {
          download: true,
          success: pilot
            ? "Case JSON downloaded. No external recipient was contacted."
            : "Synthetic case JSON downloaded. No external recipient was contacted.",
        },
      );
      if (blob) {
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `aislesignals-${incident.reference.replace(/[^a-z0-9-]/gi, "-")}.json`;
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        done(null);
      }
    } else {
      const result = await act<Incident>(
        `/incidents/${incident.id}/${kind}`,
        { expected_version: version, reason },
        {
          success:
            kind === "close"
              ? "Case closed with your recorded reason."
              : "Case reopened by a manager.",
        },
      );
      if (result) done(result);
    }
  }
  return (
    <form className="modal-form" onSubmit={submit}>
      <p>
        {kind === "export"
          ? pilot
            ? "This downloads the structured case, history and integrity digest as JSON. Video and product-interaction images are not included. Share only through your pharmacy’s authorised process."
            : "This downloads the synthetic structured case, history and integrity digest as JSON. It is not a real video evidence package and is not sent to anyone."
          : kind === "close"
            ? "Record why this case is ready to close. The reason remains in its history."
            : "Record why this closed case needs further work. The reason remains in its history."}
      </p>
      <label>
        {kind === "export" ? "Export purpose" : "Reason"}
        <textarea
          rows={4}
          required
          minLength={5}
          maxLength={kind === "export" ? 500 : 2000}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          autoFocus
        />
      </label>
      <button className="button primary full" disabled={disabled}>
        {kind === "export"
          ? "Download case JSON"
          : kind === "close"
            ? "Confirm close case"
            : "Confirm reopen case"}
        <ArrowRight size={17} />
      </button>
    </form>
  );
}
function ManualForm({
  siteId,
  disabled,
  act,
  done,
}: {
  siteId: string;
  disabled: boolean;
  act: Act;
  done: (id: string) => void;
}) {
  const pilot = useContext(PilotContext);
  const [form, setForm] = useDraft(`manual:${siteId}`, {
    title: "",
    notes: "",
  });
  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await act<Incident>("/incidents", form, {
      success: "Manual case created for review.",
    });
    if (result) {
      setForm({ title: "", notes: "" });
      done(result.id);
    }
  }
  return (
    <form className="modal-form" onSubmit={submit}>
      <div className="notice">
        {pilot
          ? "Record relevant, reviewed facts for this pharmacy. Manual records work independently of detection and the workflow shift. Do not enter patient records or unnecessary personal details."
          : "Manual records work independently of the demo shift and observation queue. Use synthetic details only."}
      </div>
      <label>
        Case title
        <input
          required
          minLength={3}
          maxLength={120}
          value={form.title}
          onChange={(e) => setForm({ ...form, title: e.target.value })}
          placeholder="A short, factual description"
        />
      </label>
      <label>
        Initial notes
        <textarea
          rows={5}
          required
          minLength={5}
          maxLength={4000}
          value={form.notes}
          onChange={(e) => setForm({ ...form, notes: e.target.value })}
          placeholder="What happened, what is known and what needs review?"
        />
      </label>
      <button className="button primary full" disabled={disabled}>
        Create manual case
        <Plus size={17} />
      </button>
    </form>
  );
}
function AssistanceForm({
  siteId,
  disabled,
  act,
  done,
}: {
  siteId: string;
  disabled: boolean;
  act: Act;
  done: () => void;
}) {
  const pilot = useContext(PilotContext);
  const [reason, setReason] = useDraft(`assistance:${siteId}`, "");
  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await act<Assistance>(
      "/assistance",
      { reason },
      { success: "Team assistance request recorded in this workspace." },
    );
    if (result) {
      setReason("");
      done();
    }
  }
  return (
    <form className="modal-form" onSubmit={submit}>
      <div className="notice amber">
        <Bell size={18} />
        <span>
          {pilot
            ? "This records a request in this installation for the active branch. Contact a colleague directly when help is needed; it does not send a notification to another laptop or call emergency services."
            : "This records a request in the demo workspace. It does not call emergency services, send a message or sound an external alarm."}
        </span>
      </div>
      <label>
        What support is needed?
        <textarea
          rows={4}
          minLength={3}
          maxLength={500}
          required
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="e.g. A second colleague at the front counter"
        />
      </label>
      <button className="button primary full" disabled={disabled}>
        Request team assistance
        <Bell size={17} />
      </button>
    </form>
  );
}
function AssistancePage({
  data,
  disabled,
  act,
  create,
}: {
  data: Bootstrap;
  disabled: boolean;
  act: Act;
  create: () => void;
}) {
  const [filter, setFilter] = useState("ACTIVE");
  const items = data.assistance.filter(
    (a) => filter === "ALL" || a.status !== "RESOLVED",
  );
  return (
    <>
      <div className="notice">
        <CircleHelp size={18} />
        <span>
          An acknowledgement means someone has seen the request. It does not
          mean a colleague has arrived. No external notifications or emergency
          integrations are connected.
        </span>
      </div>
      <div className="toolbar">
        <div className="segmented">
          {[
            ["ACTIVE", "Active requests"],
            ["ALL", "All requests"],
          ].map(([v, n]) => (
            <button
              key={v}
              aria-pressed={filter === v}
              className={filter === v ? "active" : ""}
              onClick={() => setFilter(v)}
            >
              {n}
            </button>
          ))}
        </div>
        <button className="button primary" disabled={disabled} onClick={create}>
          <Plus size={16} />
          Ask for assistance
        </button>
      </div>
      <div className="assistance-grid">
        {items.length ? (
          items.map((a) => (
            <section className="panel assistance-card" key={a.id}>
              <div className="assistance-card-top">
                <div className="event-icon">
                  <Bell size={21} />
                </div>
                <Badge value={a.status} />
              </div>
              <h2>{a.reason}</h2>
              <p>
                Requested by {a.requested_by}
                <br />
                {date(a.created_at, true)}
              </p>
              <div className="assistance-actions">
                {a.status === "REQUESTED" && (
                  <button
                    className="button secondary"
                    disabled={disabled}
                    onClick={() =>
                      void act(
                        `/assistance/${a.id}/transition`,
                        { status: "ACKNOWLEDGED" },
                        {
                          success:
                            "Request acknowledged. Arrival is not implied.",
                        },
                      )
                    }
                  >
                    <Check size={16} />
                    Acknowledge request
                  </button>
                )}
                {a.status === "ACKNOWLEDGED" && (
                  <button
                    className="button primary"
                    disabled={disabled}
                    onClick={() =>
                      void act(
                        `/assistance/${a.id}/transition`,
                        { status: "RESOLVED" },
                        { success: "Assistance request resolved." },
                      )
                    }
                  >
                    <CheckCheck size={16} />
                    Resolve request
                  </button>
                )}
                {a.status === "RESOLVED" && (
                  <span className="muted">No further action needed.</span>
                )}
              </div>
            </section>
          ))
        ) : (
          <div className="panel full-span">
            <Empty
              title="No active support requests"
              detail="If a colleague needs support, record a request and track its response."
            />
          </div>
        )}
      </div>
    </>
  );
}

function Cameras({
  data,
  manager,
  disabled,
  simulator,
}: {
  data: Bootstrap;
  manager: boolean;
  disabled: boolean;
  simulator: () => void;
}) {
  const pilot = data.mode === "pilot";
  const [sound, setSound] = useState("");
  async function soundTest() {
    try {
      const context = new AudioContext();
      await context.resume();
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.type = "sine";
      oscillator.frequency.value = 660;
      gain.gain.setValueAtTime(0.08, context.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, context.currentTime + 0.45);
      oscillator.connect(gain);
      gain.connect(context.destination);
      oscillator.start();
      oscillator.stop(context.currentTime + 0.5);
      oscillator.onended = () => void context.close();
      setSound(
        "A short tone was requested. Confirm you heard it on this laptop; the app cannot verify speaker volume.",
      );
    } catch {
      setSound(
        "Sound could not be started. Check browser permissions and the existing laptop speakers.",
      );
    }
  }
  return (
    <>
      <div className="notice amber">
        <Monitor size={19} />
        <span>
          {pilot
            ? "Connect an authorised CCTV browser tab, app window, screen, supported camera or recording in LIVE DETECTION. Native RTSP/ONVIF ingestion is not configured here. Check each view and laptop before relying on alerts."
            : "These are simulated source states. Live RTSP/ONVIF connections and continuous detection are not connected in this prototype. Existing equipment must pass compatibility and laptop workload checks."}
        </span>
      </div>
      <div className="readiness-header">
        <div>
          <h2>Existing laptop. Existing cameras.</h2>
          <p>
            {pilot
              ? "Six-pharmacy rollout: existing Windows and Mac laptops. Qualify each installation."
              : "First pilot: one Windows laptop and one Mac. No new hardware."}
          </p>
        </div>
        {manager && !pilot && (
          <button
            className="button secondary"
            disabled={disabled}
            onClick={simulator}
          >
            <SlidersHorizontal size={16} />
            Simulate source health
          </button>
        )}
      </div>
      {pilot && !data.cameras.length && (
        <div className="panel">
          <Empty
            title="No camera view has been registered"
            detail="Open LIVE DETECTION to select and test the CCTV view available on this laptop. A camera on a separate monitor needs an existing supported route to this laptop; the monitor alone is not a video connection."
          />
        </div>
      )}
      <div className="camera-grid">
        {data.cameras.map((camera) => (
          <CameraCard key={camera.id} camera={camera} />
        ))}
      </div>
      <div className="overview-grid readiness-bottom">
        <Panel
          title="Laptop readiness"
          description="Check these prerequisites on each pharmacy laptop."
        >
          <ol className="readiness-list">
            <li>
              <span>01</span>
              <div>
                <strong>Confirm the existing connection</strong>
                <p>
                  Qualify a documented RTSP/ONVIF stream or supported recorder
                  API. A camera being installed does not establish software
                  compatibility.
                </p>
              </div>
            </li>
            <li>
              <span>02</span>
              <div>
                <strong>Keep the laptop awake and available</strong>
                <p>
                  Sleep, lid closure or power loss stops local monitoring.
                  Resume must recheck the source before any coverage claim.
                </p>
              </div>
            </li>
            <li>
              <span>03</span>
              <div>
                <strong>Measure the workload</strong>
                <p>
                  Inventory OS version, architecture, storage and camera count
                  before agreeing a supported configuration.
                </p>
              </div>
            </li>
          </ol>
        </Panel>
        <Panel
          title="A private attention sound"
          description="Use only the laptop’s existing speakers."
        >
          <div className="speaker-demo">
            <div>
              <Volume2 size={30} />
            </div>
            <p>
              Test the browser sound while a colleague is present. This short
              readiness tone does not arm live alarms; configure and test those
              in LIVE DETECTION.
            </p>
            <button
              className="button secondary"
              onClick={() => void soundTest()}
            >
              <Volume2 size={17} />
              Test laptop sound
            </button>
            {sound && (
              <p role="status" className="sound-result">
                {sound}
              </p>
            )}
          </div>
          <div className="panel-bottom">
            <ShieldCheck size={15} />
            <span>
              No relay, physical alarm or door-lock control is connected.
            </span>
          </div>
        </Panel>
      </div>
    </>
  );
}
function CameraCard({ camera: c }: { camera: Camera }) {
  return (
    <section className="panel camera-card">
      <div
        className={`camera-visual ${c.status !== "DEMO_ONLINE" ? "unavailable" : ""}`}
      >
        <div className="camera-grid-lines" />
        <Video size={34} />
        <span>SIMULATED SOURCE</span>
        <div className="camera-corner tl" />
        <div className="camera-corner tr" />
        <div className="camera-corner bl" />
        <div className="camera-corner br" />
      </div>
      <div className="camera-card-body">
        <div>
          <h3>{c.name}</h3>
          <Badge value={c.status}>
            {c.status === "DEMO_ONLINE" ? "Demo ready" : label(c.status)}
          </Badge>
        </div>
        <p>{c.zone}</p>
        <div className="camera-detail">{c.detail}</div>
        <dl>
          <div>
            <dt>Connection</dt>
            <dd>Simulator</dd>
          </div>
          <div>
            <dt>Last simulated update</dt>
            <dd>{date(c.last_seen_at)}</dd>
          </div>
        </dl>
      </div>
    </section>
  );
}
function ActivityPage({ data }: { data: Bootstrap }) {
  const [query, setQuery] = useState("");
  const items = data.audit.filter((a) =>
    `${a.action} ${a.actor} ${a.detail} ${a.resource_type}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  return (
    <>
      <div className="toolbar">
        <span className="muted">
          {items.length} entries · latest 200 records · this branch only
        </span>
        <label className="search-field">
          <Search size={17} />
          <input
            aria-label="Search activity"
            placeholder="Search activity"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
      </div>
      <Panel
        title="Activity history"
        description="Server-recorded events for the signed-in pharmacy."
      >
        {items.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Who</th>
                  <th>Action</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {items.map((a) => (
                  <tr key={a.id}>
                    <td className="nowrap muted">{date(a.at, true)}</td>
                    <td>{a.actor}</td>
                    <td>
                      <span className="audit-action">{label(a.action)}</span>
                      <span className="audit-resource">
                        {label(a.resource_type)}
                      </span>
                    </td>
                    <td className="audit-detail">{a.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title="No matching activity"
            detail="Try another search or complete a workflow action."
          />
        )}
      </Panel>
    </>
  );
}
function Settings({ data }: { data: Bootstrap }) {
  const pilot = data.mode === "pilot";
  return (
    <div className="settings-grid">
      <Panel
        title="Branch profile"
        description="Access and records stay within this pharmacy branch."
      >
        <dl className="settings-dl">
          <div>
            <dt>Pharmacy</dt>
            <dd>{data.site.name}</dd>
          </div>
          <div>
            <dt>Organisation</dt>
            <dd>{data.site.organisation_name}</dd>
          </div>
          <div>
            <dt>Timezone</dt>
            <dd>{data.site.timezone}</dd>
          </div>
          <div>
            <dt>Current role</dt>
            <dd>{label(data.user.role)}</dd>
          </div>
          <div>
            <dt>Data mode</dt>
            <dd>
              <Badge value={pilot ? "LOCAL" : "SIMULATOR"}>
                {pilot ? "Local pilot · named accounts" : "Synthetic prototype"}
              </Badge>
            </dd>
          </div>
        </dl>
      </Panel>
      <section className="subscription-card">
        <span className="eyebrow">ONE PHARMACY BRANCH</span>
        <div className="subscription-price">
          €60<span>/ month</span>
        </div>
        <h2>A clear, branch-based price.</h2>
        <p>
          Agreed product pricing for a single pharmacy branch. Creating a branch
          does not automatically create a subscription or charge.
        </p>
        <div>
          <Check size={17} />
          <span>Existing Windows or Mac laptop</span>
        </div>
        <div>
          <Check size={17} />
          <span>No hardware purchase in the project scope</span>
        </div>
        <div>
          <Check size={17} />
          <span>Human review remains central</span>
        </div>
        <span className="subscription-note">
          No payment processing · tax treatment to be agreed
        </span>
      </section>
      <Panel
        title="AI and usage controls"
        description="Economic by design, explicit about what is running."
      >
        <div className="budget-summary">
          <div>
            <span>Cloud AI spend</span>
            <strong>{money(data.budget.spent_cents)}</strong>
          </div>
          <div>
            <span>Planned monthly allowance</span>
            <strong>{money(data.budget.monthly_cap_cents)}</strong>
          </div>
        </div>
        <div
          className="budget-track"
          role="meter"
          aria-label="Cloud AI allowance used"
          aria-valuemin={0}
          aria-valuemax={data.budget.monthly_cap_cents}
          aria-valuenow={data.budget.spent_cents}
        >
          <span
            style={{
              width: `${Math.min(100, (data.budget.spent_cents / data.budget.monthly_cap_cents) * 100)}%`,
            }}
          />
        </div>
        <div className="notice">
          <FileText size={18} />
          <span>
            Case report drafts use a deterministic{" "}
            <strong>local template</strong>. Optional product-interaction
            analysis in LIVE DETECTION uses the separately configured local
            vision model. Neither makes paid cloud-provider calls.
          </span>
        </div>
        <p className="settings-note">
          Measure detection quality on authorised pharmacy footage before
          relying on alerts. No identity recognition, intent prediction or
          automated criminal finding is included.
        </p>
      </Panel>
      <Panel
        title={
          pilot
            ? "Local installation and rollout checks"
            : "Prototype boundaries"
        }
        description="What needs to be qualified before pharmacy use."
      >
        <ul className="boundary-list">
          <li>
            {pilot
              ? "Named accounts are local; multi-factor authentication is not enabled"
              : "Production identity and multi-factor authentication"}
          </li>
          <li>
            {pilot
              ? "Branch permissions apply in this installation; other laptops are not synchronised"
              : "Production database isolation, retention and recovery"}
          </li>
          <li>Real camera compatibility and source reliability</li>
          <li>Signed Windows and Mac installers with site testing</li>
          <li>Measured local detection and human review performance</li>
          <li>Existing alarm API commissioning, if separately enabled</li>
        </ul>
        <div className="panel-bottom">
          <ShieldCheck size={15} />
          <span>
            {pilot
              ? "Managers can add users and manage branch access from Administration."
              : "Demo accounts are public. Start a protected pilot workspace to create individual pharmacy accounts."}
          </span>
        </div>
      </Panel>
    </div>
  );
}
const scenarios = [
  {
    value: "SHELF_EVENT",
    title: "Shelf observation",
    description: "A current observation needing human review.",
  },
  {
    value: "RETURNED_ITEM",
    title: "Item returned",
    description: "Review context that may support a benign outcome.",
  },
  {
    value: "MISSING_MEDIA",
    title: "Missing evidence",
    description: "Review an observation when its media is unavailable.",
  },
  {
    value: "HISTORICAL_EVENT",
    title: "Historical replay",
    description: "An older observation, clearly labelled without a live alert.",
  },
  {
    value: "CAMERA_OFFLINE",
    title: "Source offline",
    description: "Practice a source outage and inspect the readiness state.",
  },
  {
    value: "CAMERA_FROZEN",
    title: "Source frozen",
    description: "Practice a stale source that should not imply coverage.",
  },
  {
    value: "CAMERA_RECOVERED",
    title: "Source recovered",
    description: "Restore the simulated source to its ready state.",
  },
];
function Simulator({
  disabled,
  act,
  active,
  done,
}: {
  disabled: boolean;
  act: Act;
  active: boolean;
  done: (id: string | null) => void;
}) {
  const [scenario, setScenario] = useState("SHELF_EVENT");
  const [sourceId, setSourceId] = useState(() => crypto.randomUUID());
  const isHealth = scenario.startsWith("CAMERA_");
  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await act<Candidate | { ok: true }>(
      "/simulator",
      { scenario, source_event_id: sourceId },
      { success: "Synthetic scenario recorded." },
    );
    if (result) done("id" in result ? result.id : null);
  }
  return (
    <form className="modal-form" onSubmit={submit}>
      <div className="notice">
        Manager-only test scenarios. These create synthetic records and source
        states; they do not analyse a camera stream.
      </div>
      <label>
        Scenario
        <select
          value={scenario}
          onChange={(e) => {
            setScenario(e.target.value);
            setSourceId(crypto.randomUUID());
          }}
        >
          {scenarios.map((s) => (
            <option key={s.value} value={s.value}>
              {s.title}
            </option>
          ))}
        </select>
      </label>
      <p className="scenario-description">
        {scenarios.find((s) => s.value === scenario)?.description}
      </p>
      {!active && !isHealth && (
        <div className="notice amber">
          <span>Activate the demo shift before adding observations.</span>
          <button
            type="button"
            className="text-button"
            disabled={disabled}
            onClick={() =>
              void act(
                "/shift",
                { active: true },
                { success: "Demo workflow activated." },
              )
            }
          >
            Activate demo shift
          </button>
        </div>
      )}
      {!isHealth && (
        <p className="field-hint">
          Current observations also require a ready simulated source. If a
          source is offline or frozen, run “Source recovered” first. Historical
          replay may be tested during an outage.
        </p>
      )}
      <button
        className="button primary full"
        disabled={disabled || (!active && !isHealth)}
      >
        Run selected scenario
        <Play size={16} />
      </button>
    </form>
  );
}
