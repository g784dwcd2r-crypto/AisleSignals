import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import type { FormEvent } from "react";
import { Cloud, RefreshCw, ShieldCheck } from "lucide-react";
import { api, ApiError } from "./api";
import {
  CloudRequestScope,
  cloudConnectionRequest,
} from "./cloudConnectionRequests";
import type { CloudAction } from "./cloudConnectionRequests";
import "./cloudConnection.css";

type RemoteIdentity = {
  device_id: string;
  organisation_id: string;
  organisation_name: string;
  pharmacy_id: string;
  pharmacy_name: string;
  name: string;
  platform: "MACOS" | "WINDOWS" | "OTHER";
  app_version: string;
};
type Connection = {
  binding_id: string;
  generation: number;
  state: "ACTIVE" | "PAUSED" | "DISCONNECTED" | "RESTORED";
  origin: string;
  organisation_id: string;
  pharmacy_id: string;
  device_id: string;
  credential_available: boolean;
  error_code: string | null;
  identity: RemoteIdentity | null;
};
type Preparation = {
  preparation_id: string;
  status: "PREPARING" | "PREPARED" | "UNCERTAIN";
  expires_at: string;
  origin: string;
  local_site_id: string;
  identity: RemoteIdentity | null;
  remote_device_id: string | null;
};
type ConnectionStatus = {
  enabled: boolean;
  local_site: { id: string; name: string };
  connection: Connection | null;
  preparation: Preparation | null;
  occupied_elsewhere: boolean;
  monitoring_status: "UNKNOWN";
  delivery?: {
    pending: number;
    received: number;
    blocked: number;
    withdrawal_pending: number;
    worker_running: boolean;
  };
};
type Change = "pause" | "resume" | "disconnect";
const actionLabel = {
  pause: "Pause metadata sharing",
  resume: "Resume metadata sharing",
  disconnect: "Disconnect this branch",
};
const actionHelp = {
  pause:
    "Stop sending new observation metadata. A request already in flight may still reach the cloud. Pausing does not delete records already received.",
  resume:
    "Allow eligible possible-concealment observations from this branch to be sent as historical metadata. Unexpired observations already queued may also be delivered. Video, sampled frames, person IDs and staff notes stay on the laptop.",
  disconnect:
    "Close this local connection. Uncertain deliveries can still require cloud management review; this is not confirmation that remote records or the cloud laptop registration have been deleted.",
};

function failureText(error: unknown) {
  if (!(error instanceof ApiError))
    return "The connection request could not be confirmed. Refresh its status before taking another action.";
  const messages: Record<string, string> = {
    REAUTH_REQUIRED:
      "Your manager password was not accepted. Refresh status, then enter it again for a new attempt.",
    REAUTH_RATE_LIMITED:
      "Password checks are temporarily limited. Wait before trying again; the connection has not been confirmed.",
    CONNECTION_UNCERTAIN:
      "Registration may have succeeded. Inspect the selected cloud console’s Laptops page and revoke any unconfirmed registration before requesting a new code.",
    CONNECTION_CHECK_FAILED:
      "The cloud identity could not be verified. Sharing was not activated. Refresh status before trying again.",
    CONNECTION_STORAGE_UNAVAILABLE:
      "The laptop’s private connection storage is unavailable. Check that storage before pairing or changing this connection.",
    PREPARATION_EXPIRED:
      "The identity review expired. Inspect the cloud registration before preparing a new code.",
    CODE_ALREADY_ATTEMPTED:
      "This one-use code was already attempted. Inspect the cloud registration before requesting a new code.",
    CONNECTION_BUSY:
      "Another connection check is running. Refresh status after it completes.",
    CONNECTION_EXISTS:
      "A laptop connection already exists. Refresh status and review it before starting another.",
    CONNECTION_LIMIT:
      "The local preparation limit has been reached. Review previous cloud registrations before trying again.",
    CONNECTION_CLOSED:
      "This connection is closed. Review the old cloud registration before preparing another.",
    REMOTE_IDENTITY_CHANGED:
      "The cloud identity changed. Review the cloud registration; do not confirm the earlier details.",
    IDENTITY_CONFIRMATION_MISMATCH:
      "The local branch or cloud identity no longer matches this review. Refresh and check the exact details again.",
    CONNECTION_CHANGED:
      "The connection changed or the preparation expired. Refresh status and make a fresh decision.",
    CONNECTION_LOST:
      "The local service could not confirm this request. It may have completed. Refresh status and inspect the cloud registration before repeating anything.",
    INCOMPLETE_RESPONSE:
      "The response was incomplete. The action may have completed. Refresh status before making a new attempt.",
  };
  return (
    messages[error.code] ??
    "The connection request was rejected. Refresh status and check your manager access before making a new attempt."
  );
}

function IdentityDetails({
  identity,
  origin,
}: {
  identity: RemoteIdentity;
  origin: string;
}) {
  return (
    <dl className="cloud-identity">
      <div>
        <dt>Cloud address</dt>
        <dd>{origin}</dd>
      </div>
      <div>
        <dt>Pharmacy group</dt>
        <dd>
          {identity.organisation_name}
          <small>{identity.organisation_id}</small>
        </dd>
      </div>
      <div>
        <dt>Cloud pharmacy</dt>
        <dd>
          {identity.pharmacy_name}
          <small>{identity.pharmacy_id}</small>
        </dd>
      </div>
      <div>
        <dt>Cloud laptop</dt>
        <dd>
          {identity.name}
          <small>{identity.device_id}</small>
        </dd>
      </div>
      <div>
        <dt>Platform · app version</dt>
        <dd>
          {identity.platform} · {identity.app_version}
        </dd>
      </div>
    </dl>
  );
}

export default function CloudConnection({
  branchId,
  branchName,
  disabled = false,
}: {
  branchId: string;
  branchName: string;
  disabled?: boolean;
}) {
  const [snapshot, setSnapshot] = useState<ConnectionStatus | null>(null);
  const [checkedAt, setCheckedAt] = useState<string | null>(null);
  const [needsRefresh, setNeedsRefresh] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [origin, setOrigin] = useState("");
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [change, setChange] = useState<Change | null>(null);
  const [clock, setClock] = useState(Date.now());
  const scope = useRef(new CloudRequestScope());
  const busyRef = useRef(false);
  const disabledRef = useRef(disabled);
  disabledRef.current = disabled;

  const clearDrafts = useCallback(() => {
    setOrigin("");
    setCode("");
    setName("");
    setPassword("");
    setAgreed(false);
    setChange(null);
    setConnecting(false);
  }, []);
  const invalidate = useCallback(() => {
    scope.current.invalidate();
    busyRef.current = false;
    setBusy(false);
    clearDrafts();
    setNeedsRefresh(true);
  }, [clearDrafts]);
  const accept = useCallback(
    (result: ConnectionStatus) => {
      if (
        result.local_site?.id !== branchId ||
        (result.preparation && result.preparation.local_site_id !== branchId)
      )
        throw new Error("Connection response belongs to a different branch");
      setSnapshot(result);
      setCheckedAt(new Date().toLocaleString());
      setNeedsRefresh(false);
      setClock(Date.now());
    },
    [branchId],
  );
  const refresh = useCallback(async () => {
    if (busyRef.current || disabledRef.current) return;
    clearDrafts();
    const request = scope.current.begin();
    busyRef.current = true;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await api<ConnectionStatus>(
        "/cloud-connection",
        "GET",
        undefined,
        false,
        AbortSignal.any([request.signal, AbortSignal.timeout(12000)]),
      );
      if (request.current()) accept(result);
    } catch (failure) {
      if (request.current()) {
        setNeedsRefresh(true);
        setError(failureText(failure));
      }
    } finally {
      if (request.current()) {
        busyRef.current = false;
        setBusy(false);
      }
    }
  }, [accept, clearDrafts]);
  useLayoutEffect(() => {
    if (disabled) {
      invalidate();
      return;
    }
    void refresh();
    return () => {
      scope.current.invalidate();
      busyRef.current = false;
    };
  }, [branchId, disabled, invalidate, refresh]);
  useEffect(() => {
    const hidden = () => {
      if (document.hidden) {
        invalidate();
        setMessage(
          "Inputs cleared when this view was hidden. Refresh status before continuing.",
        );
      }
    };
    const offline = () => {
      invalidate();
      setError(
        "Connection status is unavailable offline. An earlier request may still have completed. Refresh when the local service is reachable.",
      );
    };
    document.addEventListener("visibilitychange", hidden);
    window.addEventListener("pagehide", offline);
    window.addEventListener("offline", offline);
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", hidden);
      window.removeEventListener("pagehide", offline);
      window.removeEventListener("offline", offline);
    };
  }, [invalidate]);

  const connection = snapshot?.connection;
  const preparation = snapshot?.preparation;
  const expired =
    !!preparation &&
    (!Number.isFinite(Date.parse(preparation.expires_at)) ||
      clock >= Date.parse(preparation.expires_at));
  const reviewReady =
    preparation?.status === "PREPARED" && !!preparation.identity && !expired;
  useEffect(() => {
    if (expired) {
      setPassword("");
      setAgreed(false);
    }
  }, [expired]);
  const blocked = disabled || busy || needsRefresh || !snapshot?.enabled;
  const closed =
    !connection || ["DISCONNECTED", "RESTORED"].includes(connection.state);
  const canPrepare =
    closed &&
    !snapshot?.occupied_elsewhere &&
    (!preparation || expired || preparation.status === "UNCERTAIN");

  async function submit(action: CloudAction, payload: Record<string, unknown>) {
    if (busyRef.current || blocked) return;
    const request = scope.current.begin();
    busyRef.current = true;
    setBusy(true);
    setError("");
    setMessage("");
    // Codes and passwords disappear before the network request begins.
    clearDrafts();
    try {
      const result = await cloudConnectionRequest<ConnectionStatus>(
        action,
        payload,
        request.signal,
      );
      if (!request.current()) return;
      accept(result);
      setMessage(
        action === "prepare"
          ? "Check the server-confirmed identity below. Metadata sharing has not started."
          : action === "confirm"
            ? "Connection saved with metadata sharing PAUSED. Resume explicitly when ready."
            : action === "resume"
              ? "Metadata sharing is ACTIVE. Camera monitoring status remains UNKNOWN."
              : action === "pause"
                ? "Metadata sharing is PAUSED. Received cloud records are not deleted."
                : "Local connection DISCONNECTED. Review uncertain deliveries and the old registration in the cloud console.",
      );
    } catch (failure) {
      if (request.current()) {
        setError(failureText(failure));
        setNeedsRefresh(true);
      }
    } finally {
      if (request.current()) {
        busyRef.current = false;
        setBusy(false);
      }
    }
  }
  function prepare(event: FormEvent) {
    event.preventDefault();
    void submit("prepare", {
      origin: origin.trim(),
      code: code.trim(),
      name: name.trim(),
      manager_password: password,
    });
  }
  function confirm(event: FormEvent) {
    event.preventDefault();
    if (!reviewReady || !agreed || !preparation?.identity) return;
    const identity = preparation.identity;
    void submit("confirm", {
      preparation_id: preparation.preparation_id,
      origin: preparation.origin,
      organisation_id: identity.organisation_id,
      pharmacy_id: identity.pharmacy_id,
      device_id: identity.device_id,
      local_site_id: branchId,
      manager_password: password,
    });
  }
  function applyChange(event: FormEvent) {
    event.preventDefault();
    if (!change || !connection) return;
    void submit(change, {
      binding_id: connection.binding_id,
      expected_generation: connection.generation,
      manager_password: password,
    });
  }
  function cancel() {
    const pending = busyRef.current;
    invalidate();
    setMessage(
      pending
        ? "Cancelled locally. A request may already have reached the service; refresh status before continuing."
        : "Inputs cleared. Refresh status before continuing. A prepared cloud registration is not revoked by clearing this form.",
    );
  }
  const passwordField = (
    <label>
      Your manager password
      <input
        type="password"
        value={password}
        onChange={(event) => setPassword(event.target.value)}
        autoComplete="off"
        maxLength={256}
        required
        disabled={blocked}
      />
    </label>
  );

  return (
    <div className="cloud-connection">
      <section
        className="panel cloud-summary"
        aria-labelledby="cloud-status-heading"
      >
        <div className="cloud-heading">
          <div>
            <h2 id="cloud-status-heading">
              <Cloud size={21} /> Connection for {branchName}
            </h2>
            <p>
              Local branch ID: <span className="cloud-id">{branchId}</span>
            </p>
          </div>
          <button
            type="button"
            className="button secondary"
            disabled={busy || disabled}
            onClick={() => void refresh()}
          >
            <RefreshCw size={16} /> Refresh connection status
          </button>
        </div>
        <div className="cloud-state">
          <strong>
            {needsRefresh
              ? "Status needs checking"
              : connection
                ? `Metadata sharing: ${connection.state}`
                : "No cloud connection"}
          </strong>
          <span>Camera monitoring: UNKNOWN</span>
        </div>
        {checkedAt && (
          <p className="cloud-caption">
            Last checked {checkedAt}. This is the last saved connection state.
          </p>
        )}
        {snapshot?.delivery && (
          <>
            <dl
              className="cloud-identity"
              aria-label="Metadata delivery status"
            >
              <div>
                <dt>Queued observations</dt>
                <dd>{snapshot.delivery.pending}</dd>
              </div>
              <div>
                <dt>Cloud receipts recorded</dt>
                <dd>{snapshot.delivery.received}</dd>
              </div>
              <div>
                <dt>Blocked items</dt>
                <dd>{snapshot.delivery.blocked}</dd>
              </div>
              <div>
                <dt>Unresolved withdrawals</dt>
                <dd>{snapshot.delivery.withdrawal_pending}</dd>
              </div>
              <div>
                <dt>Sender service</dt>
                <dd>
                  {snapshot.delivery.worker_running ? "Running" : "Not running"}
                </dd>
              </div>
            </dl>
            <p className="cloud-caption">
              Counts apply to this saved connection. Blocked items can include
              unresolved withdrawals. A receipt records delivery, not current
              camera coverage or remote deletion.
            </p>
          </>
        )}
        <p>
          A cloud connection does not connect CCTV, start detection or arm an
          alarm. Check those separately in LIVE DETECTION.
        </p>
        {message && (
          <p role="status" className="cloud-message">
            {message}
          </p>
        )}
        {error && (
          <p role="alert" className="cloud-warning">
            {error}
          </p>
        )}
        {busy && (
          <p role="status">Checking the connection… No automatic retry.</p>
        )}
        {snapshot?.occupied_elsewhere && (
          <p className="cloud-warning">
            This laptop is already connected for another local branch. A manager
            for that branch must review its connection first.
          </p>
        )}
        {connection?.identity && (
          <IdentityDetails
            identity={connection.identity}
            origin={connection.origin}
          />
        )}
        {connection && !connection.identity && (
          <dl className="cloud-identity">
            <div>
              <dt>Cloud address</dt>
              <dd>{connection.origin}</dd>
            </div>
            <div>
              <dt>Cloud group ID</dt>
              <dd>{connection.organisation_id}</dd>
            </div>
            <div>
              <dt>Cloud pharmacy ID</dt>
              <dd>{connection.pharmacy_id}</dd>
            </div>
            <div>
              <dt>Cloud laptop ID</dt>
              <dd>{connection.device_id}</dd>
            </div>
          </dl>
        )}
        {connection &&
          ((!closed && !connection.credential_available) ||
            connection.error_code ||
            connection.state === "RESTORED") && (
            <p className="cloud-warning">
              {connection.state === "RESTORED"
                ? "This connection came from an offline restore. Sharing cannot resume automatically; review the prior cloud device and unresolved deliveries."
                : !closed && !connection.credential_available
                  ? "Private connection credentials are unavailable. Sharing cannot be verified. Review the laptop storage and the cloud registration."
                  : "This connection needs management review before sharing resumes."}
              {connection.error_code && (
                <small>Reported reason: {connection.error_code}</small>
              )}
            </p>
          )}
        {!closed && !change && (
          <div className="cloud-actions">
            {(connection?.state === "ACTIVE"
              ? ["pause", "disconnect"]
              : ["resume", "disconnect"]
            ).map((action) => (
              <button
                key={action}
                type="button"
                className="button secondary"
                disabled={
                  blocked ||
                  (action === "resume" && !connection?.credential_available)
                }
                onClick={() => {
                  setPassword("");
                  setChange(action as Change);
                }}
              >
                {actionLabel[action as Change]}
              </button>
            ))}
          </div>
        )}
        {change && (
          <form
            className="cloud-form"
            aria-label={actionLabel[change]}
            onSubmit={applyChange}
          >
            <h3>{actionLabel[change]}</h3>
            <p>{actionHelp[change]}</p>
            {passwordField}
            <div className="cloud-actions">
              <button
                type="submit"
                className="button primary"
                disabled={blocked}
              >
                {actionLabel[change]}
              </button>
              <button
                type="button"
                className="button secondary"
                onClick={cancel}
              >
                Cancel
              </button>
            </div>
          </form>
        )}
        {canPrepare && !connecting && (
          <button
            type="button"
            className="button primary"
            disabled={blocked}
            onClick={() => {
              clearDrafts();
              setConnecting(true);
            }}
          >
            Connect this branch
          </button>
        )}
        {(busy || connecting) && (
          <button type="button" className="button secondary" onClick={cancel}>
            Clear inputs and cancel
          </button>
        )}
      </section>

      {preparation && !connecting && (
        <section
          className="panel cloud-review"
          aria-labelledby="cloud-review-heading"
        >
          <h2 id="cloud-review-heading">Review the exact connection</h2>
          <p>
            Local pharmacy: <strong>{snapshot?.local_site.name}</strong> ·{" "}
            <span className="cloud-id">{branchId}</span>
          </p>
          {preparation.identity && (
            <IdentityDetails
              identity={preparation.identity}
              origin={preparation.origin}
            />
          )}
          <p>
            Review expires {new Date(preparation.expires_at).toLocaleString()}.
            Cancelling or expiry does not revoke the cloud laptop registration.
          </p>
          {expired && (
            <p role="status" className="cloud-warning">
              This review has expired. Inspect the registration in the cloud
              console before requesting a new code.
            </p>
          )}
          {preparation.status === "PREPARING" && (
            <p className="cloud-warning">
              The previous registration attempt is still being checked. Refresh
              status; do not submit the code again.
            </p>
          )}
          {preparation.status === "UNCERTAIN" && (
            <p role="alert" className="cloud-warning">
              Registration outcome is uncertain. In the selected cloud console,
              open Laptops and revoke any unconfirmed device before obtaining a
              new code.{" "}
              {preparation.remote_device_id && (
                <>Reported device ID: {preparation.remote_device_id}.</>
              )}
            </p>
          )}
          {reviewReady && (
            <form
              className="cloud-form"
              aria-label="Confirm cloud pharmacy connection"
              onSubmit={confirm}
            >
              <label className="cloud-check">
                <input
                  type="checkbox"
                  checked={agreed}
                  disabled={blocked}
                  onChange={(event) => setAgreed(event.target.checked)}
                />
                I checked the local branch, cloud address, pharmacy group,
                pharmacy and laptop shown above.
              </label>
              {passwordField}
              <div className="cloud-actions">
                <button
                  type="submit"
                  className="button primary"
                  disabled={blocked || !agreed}
                >
                  Confirm connection · keep sharing paused
                </button>
                <button
                  type="button"
                  className="button secondary"
                  onClick={cancel}
                >
                  Clear review inputs
                </button>
              </div>
            </form>
          )}
        </section>
      )}

      {connecting && (
        <section className="panel">
          <h2>Connect this branch</h2>
          <p>
            Get a fresh one-use code from the chosen pharmacy’s Laptops page in
            your cloud console. The local service checks the registered identity
            before you confirm it.
          </p>
          <form
            className="cloud-form"
            aria-label="Prepare cloud pharmacy connection"
            onSubmit={prepare}
          >
            <label>
              Cloud HTTPS address
              <input
                type="url"
                placeholder="https://your-cloud.example"
                value={origin}
                onChange={(event) => setOrigin(event.target.value)}
                autoComplete="off"
                spellCheck={false}
                required
                disabled={blocked}
                maxLength={2048}
                aria-describedby="cloud-origin-help"
              />
            </label>
            <p id="cloud-origin-help" className="cloud-caption">
              Enter only the HTTPS origin, without a path, query or sign-in
              details.
            </p>
            <label>
              One-use connection code
              <input
                type="password"
                value={code}
                onChange={(event) => setCode(event.target.value)}
                autoComplete="off"
                minLength={43}
                maxLength={43}
                pattern="[A-Za-z0-9_-]{43}"
                required
                disabled={blocked}
              />
            </label>
            <label>
              Laptop name
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                autoComplete="off"
                maxLength={100}
                required
                disabled={blocked}
              />
            </label>
            {passwordField}
            <div className="cloud-actions">
              <button
                type="submit"
                className="button primary"
                disabled={blocked}
              >
                Check cloud identity
              </button>
              <button
                type="button"
                className="button secondary"
                onClick={cancel}
              >
                Cancel
              </button>
            </div>
          </form>
        </section>
      )}
      <div className="cloud-boundary">
        <ShieldCheck size={19} />
        <p>
          Only eligible possible-concealment observation metadata is shared by
          default, for staff review. This is not a theft finding. Monitoring,
          sound commissioning, evidence and camera quality remain separate
          checks.
        </p>
      </div>
    </div>
  );
}
