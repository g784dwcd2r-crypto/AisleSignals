import { cloneElement, useEffect, useId, useRef, useState } from "react";
import type { FormEvent, ReactElement, ReactNode } from "react";
import {
  ArrowRight,
  Check,
  Copy,
  Inbox,
  LoaderCircle,
  RefreshCw,
  Search,
  TriangleAlert,
  X,
} from "lucide-react";
import { ApiError, client, errorMessage, isCancelled } from "./api";

export function label(value: string): string {
  return value
    .toLowerCase()
    .replaceAll("_", " ")
    .replace(/^./, (letter) => letter.toUpperCase());
}
export function when(value: string | null | undefined): string {
  if (!value) return "Not yet reported";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Unknown"
    : new Intl.DateTimeFormat("en-IE", {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: "Europe/Dublin",
      }).format(date);
}
export function ago(value: string | null | undefined): string {
  if (!value) return "Not yet connected";
  const seconds = Math.max(
    0,
    Math.round((Date.now() - new Date(value).getTime()) / 1000),
  );
  if (!Number.isFinite(seconds)) return "Unknown";
  if (seconds < 60) return "Just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}
export function Badge({ value }: { value: string }) {
  const tone = ["ONLINE", "CLOSED", "REVIEWED", "NORMAL_SHOPPING"].includes(
    value,
  )
    ? "good"
    : ["OPEN", "DEGRADED", "UNCLEAR", "ACKNOWLEDGED"].includes(value)
      ? "warning"
      : ["OFFLINE", "REVOKED", "SUSPECTED_INCIDENT"].includes(value)
        ? "danger"
        : "neutral";
  return (
    <span className={`badge ${tone}`}>
      <span />
      {label(value)}
    </span>
  );
}
export function Button({
  children,
  busy = false,
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { busy?: boolean }) {
  return (
    <button
      className={`button ${className}`}
      type="button"
      {...props}
      disabled={props.disabled || busy}
      aria-busy={busy || undefined}
    >
      {busy && <LoaderCircle size={16} className="spin" />}
      {children}
    </button>
  );
}
export function ErrorNotice({
  error,
  retry,
}: {
  error: unknown;
  retry?: () => void;
}) {
  return (
    <div className="notice danger-notice" role="alert">
      <TriangleAlert size={19} />
      <div>{typeof error === "string" ? error : errorMessage(error)}</div>
      {retry && (
        <button className="text-button" onClick={retry}>
          Try again
        </button>
      )}
    </div>
  );
}
export function Empty({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-icon">
        <Inbox size={25} />
      </span>
      <h2>{title}</h2>
      <p>{children}</p>
      {action}
    </div>
  );
}
export function Loading({
  text = "Loading workspace data…",
}: {
  text?: string;
}) {
  return (
    <div className="loading" role="status">
      <LoaderCircle className="spin" size={22} />
      <span>{text}</span>
    </div>
  );
}
export function Field({
  label: text,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactElement<{ id?: string; "aria-describedby"?: string }>;
}) {
  const generatedId = useId();
  const id = children.props.id || generatedId;
  return (
    <div className="field">
      <label htmlFor={id}>{text}</label>
      {cloneElement(children, {
        id,
        "aria-describedby": hint ? `${id}-hint` : undefined,
      })}
      {hint && <small id={`${id}-hint`}>{hint}</small>}
    </div>
  );
}
export function Toolbar({
  search,
  setSearch,
  children,
}: {
  search: string;
  setSearch: (value: string) => void;
  children?: ReactNode;
}) {
  return (
    <div className="toolbar">
      <label className="search">
        <Search size={17} />
        <input
          aria-label="Search this list"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search this list…"
        />
      </label>
      <div className="toolbar-filters">{children}</div>
    </div>
  );
}
export function Table<T>({
  rows,
  columns,
  rowKey,
  onOpen,
}: {
  rows: T[];
  columns: { title: string; cell: (row: T) => ReactNode }[];
  rowKey: (row: T) => string;
  onOpen?: (row: T) => void;
}) {
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.title}>{column.title}</th>
            ))}
            {onOpen && (
              <th>
                <span className="sr-only">Details</span>
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)}>
              {columns.map((column) => (
                <td key={column.title}>{column.cell(row)}</td>
              ))}
              {onOpen && (
                <td>
                  <button
                    className="row-open"
                    onClick={() => onOpen(row)}
                    aria-label="Open record details"
                  >
                    <ArrowRight size={17} />
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
export function Drawer({
  title,
  subtitle,
  children,
  close,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  close: () => void;
}) {
  const titleId = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const node = dialog.current;
    const opener = document.activeElement;
    node?.showModal();
    return () => {
      node?.close();
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, []);
  return (
    <dialog
      className="drawer"
      ref={dialog}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <div className="drawer-inner">
        <header>
          <div>
            <p className="eyebrow">Workspace details</p>
            <h2 id={titleId}>{title}</h2>
            {subtitle && <p className="muted">{subtitle}</p>}
          </div>
          <button
            className="icon-button"
            aria-label="Close details"
            onClick={close}
          >
            <X size={21} />
          </button>
        </header>
        <div className="drawer-body">{children}</div>
      </div>
    </dialog>
  );
}
export function Detail({
  label: text,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="detail">
      <dt>{text}</dt>
      <dd>{children}</dd>
    </div>
  );
}
export function CopyValue({
  value,
  title = "One-time code",
  expires,
  hint = "Shown once. Share it privately with the intended person.",
}: {
  value: string;
  title?: string;
  expires?: string;
  hint?: string;
}) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  return (
    <div className="secret-panel">
      <h3>{title}</h3>
      <p>{hint}</p>
      <textarea
        aria-label={title}
        readOnly
        value={value}
        rows={3}
        onFocus={(event) => event.target.select()}
      />
      <Button
        className="secondary"
        onClick={() => {
          if (!navigator.clipboard) {
            setFailed(true);
            return;
          }
          void navigator.clipboard
            .writeText(value)
            .then(() => {
              setCopied(true);
              setFailed(false);
            })
            .catch(() => setFailed(true));
        }}
      >
        {copied ? <Check size={16} /> : <Copy size={16} />}
        {copied ? "Copied" : "Copy securely"}
      </Button>
      {failed && (
        <p role="status">Select and copy the text above using your keyboard.</p>
      )}
      {expires && <small>Expires {when(expires)} · Dublin time</small>}
    </div>
  );
}
export function useResource<T>(path: string, revision = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [version, setVersion] = useState(0);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const oldPath = useRef(path);
  useEffect(() => {
    const controller = new AbortController();
    if (oldPath.current !== path) {
      setData(null);
      setUpdatedAt(null);
      oldPath.current = path;
    }
    setLoading(true);
    setError(null);
    client
      .get<T>(path, { signal: controller.signal })
      .then((value) => {
        if (!controller.signal.aborted) {
          setData(value);
          setUpdatedAt(new Date().toISOString());
        }
      })
      .catch((reason: unknown) => {
        if (!isCancelled(reason) && !controller.signal.aborted) {
          if (
            reason instanceof ApiError &&
            [401, 403, 404].includes(reason.status)
          ) {
            setData(null);
            setUpdatedAt(null);
          }
          setError(reason);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [path, revision, version]);
  return {
    data,
    error,
    loading,
    updatedAt,
    refresh: () => setVersion((value) => value + 1),
  };
}
export function Refresh({
  loading,
  refresh,
}: {
  loading: boolean;
  refresh: () => void;
}) {
  return (
    <button
      className="icon-button"
      aria-label="Refresh data"
      disabled={loading}
      onClick={refresh}
    >
      <RefreshCw size={17} className={loading ? "spin" : ""} />
    </button>
  );
}
export function useMutation(done: () => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const abort = useRef<AbortController | null>(null);
  useEffect(() => () => abort.current?.abort(), []);
  const run = async (task: (signal: AbortSignal) => Promise<unknown>) => {
    if (abort.current) return;
    const controller = new AbortController();
    abort.current = controller;
    setBusy(true);
    setError(null);
    try {
      await task(controller.signal);
      if (!controller.signal.aborted) done();
    } catch (reason) {
      if (!controller.signal.aborted && !isCancelled(reason)) setError(reason);
    } finally {
      if (!controller.signal.aborted) {
        setBusy(false);
        abort.current = null;
      }
    }
  };
  return {
    busy,
    error,
    run,
    submit:
      (task: (signal: AbortSignal) => Promise<unknown>) =>
      (event: FormEvent) => {
        event.preventDefault();
        void run(task);
      },
  };
}
