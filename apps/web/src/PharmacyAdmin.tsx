import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Plus, RefreshCw, ShieldCheck, Users, X } from "lucide-react";
import { api, ApiError } from "./api";
import { adminRequest } from "./adminRequests";
import type { AllowedSite, Role, User } from "./types";
import "./admin.css";

type Account = {
  id: string;
  name: string;
  email: string;
  enabled: boolean;
  version: string;
  branches: { site_id: string; site_name: string; role: Role }[];
  can_manage_account: boolean;
};
type Operation = {
  kind: "branch" | "user" | "access" | "enabled" | "password";
  target?: Account;
};
const titles = {
  branch: "Add pharmacy branch",
  user: "Add user",
  access: "Change branch access",
  enabled: "Change account status",
  password: "Reset account password",
};
const failureText = (error: unknown) =>
  error instanceof Error
    ? error.message
    : "The request could not complete. Check the local service.";

function AccountForm({
  operation,
  sites,
  siteId,
  onClose,
  onSubmit,
}: {
  operation: Operation;
  sites: AllowedSite[];
  siteId: string;
  onClose: () => void;
  onSubmit: (path: string, payload: Record<string, unknown>) => Promise<void>;
}) {
  const { kind, target } = operation;
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [managerPassword, setManagerPassword] = useState("");
  const [grants, setGrants] = useState<Record<string, Role>>({
    [siteId]: "REVIEWER",
  });
  const [branchId, setBranchId] = useState(
    target?.branches[0]?.site_id ?? siteId,
  );
  const [role, setRole] = useState<Role | "REMOVE">(
    target?.branches[0]?.role ?? "REVIEWER",
  );
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  busyRef.current = busy;
  const [error, setError] = useState("");
  const mounted = useRef(true);
  const dialog = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    mounted.current = true;
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.querySelector<HTMLElement>("input,select,button")?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busyRef.current) closeRef.current();
      if (event.key === "Tab") {
        const fields = Array.from(
          dialog.current?.querySelectorAll<HTMLElement>(
            "button:not(:disabled),input:not(:disabled),select:not(:disabled)",
          ) ?? [],
        );
        if (event.shiftKey && document.activeElement === fields[0]) {
          event.preventDefault();
          fields.at(-1)?.focus();
        } else if (
          !event.shiftKey &&
          document.activeElement === fields.at(-1)
        ) {
          event.preventDefault();
          fields[0]?.focus();
        }
      }
    };
    const hidden = () => {
      if (document.hidden) {
        setPassword("");
        setConfirmation("");
        setManagerPassword("");
      }
    };
    document.addEventListener("keydown", key);
    document.addEventListener("visibilitychange", hidden);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      mounted.current = false;
      document.removeEventListener("keydown", key);
      document.removeEventListener("visibilitychange", hidden);
      document.body.style.overflow = overflow;
      previous?.focus();
    };
  }, []);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if ((kind === "user" || kind === "password") && password !== confirmation) {
      setPassword("");
      setConfirmation("");
      setManagerPassword("");
      setError("The new passphrases did not match. Enter them again.");
      return;
    }
    if (kind === "user" && !Object.keys(grants).length) {
      setPassword("");
      setConfirmation("");
      setManagerPassword("");
      setError("Assign at least one pharmacy branch.");
      return;
    }
    let path = "",
      payload: Record<string, unknown> = {
        manager_password: managerPassword,
        ...(target ? { expected_version: target.version } : {}),
      };
    if (kind === "branch") {
      path = "/admin/sites";
      payload.name = name.trim();
    }
    if (kind === "user") {
      path = "/admin/users";
      payload = {
        ...payload,
        name: name.trim(),
        email: email.trim(),
        password,
        branches: Object.entries(grants).map(([site_id, grantRole]) => ({
          site_id,
          role: grantRole,
        })),
      };
    }
    if (kind === "access" && target) {
      path = `/admin/users/${target.id}/access`;
      payload = {
        ...payload,
        site_id: branchId,
        role: role === "REMOVE" ? null : role,
      };
    }
    if (kind === "enabled" && target) {
      path = `/admin/users/${target.id}/enabled`;
      payload.enabled = !target.enabled;
    }
    if (kind === "password" && target) {
      path = `/admin/users/${target.id}/password`;
      payload.password = password;
    }
    setPassword("");
    setConfirmation("");
    setManagerPassword("");
    setBusy(true);
    setError("");
    try {
      await onSubmit(path, payload);
    } catch (failure) {
      if (mounted.current) setError(failureText(failure));
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  return (
    <div className="modal-backdrop">
      <div
        className="modal admin-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="admin-dialog-title"
        ref={dialog}
      >
        <div className="modal-header">
          <h2 id="admin-dialog-title">{titles[kind]}</h2>
          <button
            type="button"
            className="icon-button"
            aria-label="Close administration dialog"
            onClick={onClose}
            disabled={busy}
          >
            <X size={20} />
          </button>
        </div>
        <form onSubmit={submit} className="admin-form">
          {target && (
            <p className="admin-target">
              <strong>{target.name}</strong>
              <br />
              {target.email}
            </p>
          )}
          {(kind === "branch" || kind === "user") && (
            <label>
              {kind === "branch" ? "Pharmacy branch name" : "Full name"}
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                minLength={2}
                maxLength={120}
                required
                autoComplete={kind === "user" ? "name" : "off"}
              />
            </label>
          )}
          {kind === "branch" && (
            <p className="admin-hint">
              Creates a branch in the current pharmacy group on this
              installation and assigns you as its manager. This does not create
              a subscription charge or connect another laptop.
            </p>
          )}
          {kind === "user" && (
            <>
              <label>
                Email address
                <input
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  maxLength={254}
                  required
                  autoComplete="off"
                />
              </label>
              <fieldset className="admin-grants">
                <legend>Assigned pharmacy branches</legend>
                {sites.map((site) => (
                  <div key={site.id} className="admin-grant-row">
                    <label className="admin-check">
                      <input
                        type="checkbox"
                        checked={!!grants[site.id]}
                        onChange={(event) =>
                          setGrants((previous) => {
                            const next = { ...previous };
                            if (event.target.checked)
                              next[site.id] = "REVIEWER";
                            else delete next[site.id];
                            return next;
                          })
                        }
                      />
                      {site.name}
                    </label>
                    {grants[site.id] && (
                      <label>
                        Role for {site.name}
                        <select
                          value={grants[site.id]}
                          onChange={(event) =>
                            setGrants((previous) => ({
                              ...previous,
                              [site.id]: event.target.value as Role,
                            }))
                          }
                        >
                          <option value="REVIEWER">Reviewer</option>
                          <option value="MANAGER">Manager</option>
                        </select>
                      </label>
                    )}
                  </div>
                ))}
              </fieldset>
            </>
          )}
          {kind === "access" && (
            <>
              <label>
                Pharmacy branch
                <select
                  value={branchId}
                  onChange={(event) => {
                    setBranchId(event.target.value);
                    setRole(
                      target?.branches.find(
                        (branch) => branch.site_id === event.target.value,
                      )?.role ?? "REVIEWER",
                    );
                  }}
                >
                  {sites.map((site) => (
                    <option key={site.id} value={site.id}>
                      {site.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Branch role
                <select
                  value={role}
                  onChange={(event) =>
                    setRole(event.target.value as Role | "REMOVE")
                  }
                >
                  <option value="REVIEWER">Reviewer</option>
                  <option value="MANAGER">Manager</option>
                  <option value="REMOVE">Remove access to this branch</option>
                </select>
              </label>
              <p className="admin-hint">
                Access changes end this account’s active sessions. The last
                enabled manager and final branch assignment are protected.
              </p>
            </>
          )}
          {kind === "enabled" && (
            <p>
              {target?.enabled
                ? "Disable this account and end its sessions. Records and review history are retained."
                : "Enable this account so the person can sign in with their assigned branch access."}
            </p>
          )}
          {(kind === "user" || kind === "password") && (
            <>
              <label>
                New account passphrase
                <input
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  minLength={14}
                  maxLength={256}
                  required
                  autoComplete="new-password"
                />
              </label>
              <label>
                Confirm new account passphrase
                <input
                  type="password"
                  value={confirmation}
                  onChange={(event) => setConfirmation(event.target.value)}
                  minLength={14}
                  maxLength={256}
                  required
                  autoComplete="new-password"
                />
              </label>
              <p className="admin-hint">
                At least 14 characters. Provide this individual passphrase
                securely to the account holder. It is not displayed or stored in
                the page after submission.
              </p>
            </>
          )}
          <label>
            Your manager password
            <input
              type="password"
              value={managerPassword}
              onChange={(event) => setManagerPassword(event.target.value)}
              required
              maxLength={256}
              autoComplete="current-password"
            />
          </label>
          <p className="admin-hint">
            Re-enter your own password to authorise this change. Secret fields
            clear when submitted, cancelled, hidden or closed.
          </p>
          {error && (
            <p className="inline-error" role="alert">
              {error}
            </p>
          )}
          <div className="admin-form-actions">
            <button
              type="button"
              className="button secondary"
              onClick={onClose}
              disabled={busy}
            >
              Cancel
            </button>
            <button className="button primary" disabled={busy}>
              {busy
                ? "Saving…"
                : kind === "enabled"
                  ? target?.enabled
                    ? "Disable account"
                    : "Enable account"
                  : kind === "branch"
                    ? "Create branch"
                    : kind === "user"
                      ? "Create user"
                      : "Save account change"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default function PharmacyAdmin({
  user,
  siteId,
  disabled,
  onAccessChanged,
  onOwnAccountChanged,
}: {
  user: User;
  siteId: string;
  disabled: boolean;
  onAccessChanged: () => Promise<void>;
  onOwnAccountChanged: () => void;
}) {
  const [sites, setSites] = useState<AllowedSite[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [operation, setOperation] = useState<Operation | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [search, setSearch] = useState("");
  const mounted = useRef(true);
  const epoch = useRef(0);
  async function refresh() {
    const request = ++epoch.current;
    setLoading(true);
    try {
      const [branchResult, accountResult] = await Promise.all([
        api<{ sites: AllowedSite[] }>("/admin/sites"),
        api<{ users: Account[] }>("/admin/users"),
      ]);
      if (!mounted.current || request !== epoch.current) return;
      setSites(branchResult.sites);
      setAccounts(accountResult.users);
      setError("");
    } catch (failure) {
      if (mounted.current && request === epoch.current)
        setError(failureText(failure));
    } finally {
      if (mounted.current && request === epoch.current) setLoading(false);
    }
  }
  useEffect(() => {
    mounted.current = true;
    void refresh();
    return () => {
      mounted.current = false;
      epoch.current++;
    };
  }, []);
  async function submit(path: string, payload: Record<string, unknown>) {
    if (disabled || !operation)
      throw new Error(
        "The workspace is unavailable. Reconnect before changing accounts.",
      );
    const active = operation;
    try {
      await adminRequest(path, payload);
    } catch (failure) {
      if (
        failure instanceof ApiError &&
        failure.code === "ADMIN_ACCOUNT_CHANGED" &&
        mounted.current
      ) {
        setOperation(null);
        setNotice("");
        await refresh();
        if (mounted.current)
          setError(
            "This account changed while you were editing. The latest account details have been loaded. Review them before making another change.",
          );
        return;
      }
      throw failure;
    }
    if (!mounted.current) return;
    setOperation(null);
    if (active.target?.id === user.id) {
      onOwnAccountChanged();
      return;
    }
    setNotice(
      active.kind === "branch"
        ? "Pharmacy branch created. You can select it in the branch picker."
        : active.kind === "user"
          ? "Individual account created with the selected branch access."
          : "Account updated. The account holder must sign in again after access or password changes.",
    );
    await refresh();
    if (active.kind === "branch") {
      try {
        await onAccessChanged();
      } catch {
        if (mounted.current)
          setError(
            "The branch was created, but your branch picker could not refresh. Refresh the workspace before continuing.",
          );
      }
    }
  }
  const filtered = accounts.filter((account) =>
    `${account.name} ${account.email}`
      .toLowerCase()
      .includes(search.trim().toLowerCase()),
  );
  return (
    <section
      className="pharmacy-admin"
      aria-label="Pharmacy account administration"
    >
      <div className="admin-intro">
        <ShieldCheck size={23} />
        <p>
          Manage named accounts for the pharmacy branches where you are a
          manager. Accounts and branch settings are local to this installation;
          they do not synchronise other laptops.
        </p>
      </div>
      <div className="admin-toolbar">
        <button
          className="button primary"
          disabled={disabled || loading || !sites.length}
          onClick={() => setOperation({ kind: "user" })}
        >
          <Plus size={17} />
          Add user
        </button>
        <button
          className="button secondary"
          disabled={disabled || loading}
          onClick={() => setOperation({ kind: "branch" })}
        >
          <Plus size={17} />
          Add pharmacy branch
        </button>
        <button
          className="button secondary"
          disabled={disabled || loading}
          onClick={() => void refresh()}
        >
          <RefreshCw size={16} />
          Refresh administration
        </button>
      </div>
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      <section
        className="admin-branches"
        aria-labelledby="managed-branches-heading"
      >
        <h2 id="managed-branches-heading">Branches you manage</h2>
        <div className="admin-branch-list">
          {sites.map((site) => (
            <span key={site.id}>
              {site.name}
              <small>{site.organisation_name}</small>
            </span>
          ))}
        </div>
      </section>
      <div className="admin-account-heading">
        <h2>
          <Users size={20} />
          Individual accounts <span>({accounts.length})</span>
        </h2>
        <label>
          Find an account
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Name or email"
          />
        </label>
      </div>
      {loading && <p role="status">Loading assigned branches and accounts…</p>}
      {!loading && !filtered.length && (
        <p className="notice">No accounts match this view.</p>
      )}
      <div className="admin-account-list">
        {filtered.map((account) => (
          <article
            key={account.id}
            className="admin-account"
            aria-label={`Account ${account.name}`}
          >
            <div className="admin-account-title">
              <div>
                <h3>
                  {account.name}
                  {account.id === user.id ? " (you)" : ""}
                </h3>
                <p>{account.email}</p>
              </div>
              <span
                className={`badge ${account.enabled ? "badge-neutral" : "badge-offline"}`}
              >
                {account.enabled ? "Enabled" : "Disabled"}
              </span>
            </div>
            <ul>
              {account.branches.map((branch) => (
                <li key={branch.site_id}>
                  <strong>{branch.site_name}</strong> ·{" "}
                  {branch.role === "MANAGER" ? "Manager" : "Reviewer"}
                </li>
              ))}
            </ul>
            {!account.can_manage_account && (
              <p className="admin-hint">
                This account also has access outside your management scope. You
                can change its visible branch access; account-wide status and
                password changes require an authorised manager.
              </p>
            )}
            <div className="admin-account-actions">
              <button
                className="button secondary"
                disabled={disabled || loading}
                onClick={() =>
                  setOperation({ kind: "access", target: account })
                }
              >
                Change branch access
              </button>
              <button
                className="button secondary"
                disabled={disabled || loading || !account.can_manage_account}
                onClick={() =>
                  setOperation({ kind: "password", target: account })
                }
              >
                Reset password
              </button>
              <button
                className="button secondary"
                disabled={disabled || loading || !account.can_manage_account}
                onClick={() =>
                  setOperation({ kind: "enabled", target: account })
                }
              >
                {account.enabled ? "Disable user" : "Enable user"}
              </button>
            </div>
          </article>
        ))}
      </div>
      {operation && (
        <AccountForm
          key={`${operation.kind}:${operation.target?.id ?? "new"}`}
          operation={operation}
          sites={sites}
          siteId={siteId}
          onClose={() => setOperation(null)}
          onSubmit={submit}
        />
      )}
    </section>
  );
}
