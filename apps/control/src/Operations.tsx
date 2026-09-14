import { useState } from "react";
import {
  ArrowRight,
  Bell,
  Building2,
  Check,
  CheckCheck,
  ClipboardCheck,
  Clock3,
  Laptop,
  Link2,
  Plus,
  ShieldAlert,
  Signal,
  Unplug,
} from "lucide-react";
import { client } from "./api";
import { connectionStatus } from "./health";
import type { WorkspaceProps } from "./Admin";
import type {
  Alert,
  Collection,
  Dashboard,
  Device,
  Incident,
  OneTimeToken,
  Outcome,
  Page,
} from "./types";
import {
  Badge,
  Button,
  CopyValue,
  Detail,
  Drawer,
  Empty,
  ErrorNotice,
  Field,
  Loading,
  Table,
  Toolbar,
  ago,
  label,
  useMutation,
  useResource,
  when,
} from "./ui";

function path(name: string, scope: string, status = "") {
  const query = new URLSearchParams();
  if (scope) query.set("pharmacy_id", scope);
  if (status && status !== "ALL") query.set("status", status);
  return `/${name}${query.size ? `?${query}` : ""}`;
}
function Monitoring({ device }: { device: Device }) {
  const known = connectionStatus(device) === "ONLINE";
  return (
    <span
      className={`monitoring ${known && device.monitoring_status === "ACTIVE" ? "reported-active" : ""}`}
    >
      <Signal size={14} />
      {known ? label(device.monitoring_status) : "Unconfirmed"}
    </span>
  );
}
function AlertRow({ alert, open }: { alert: Alert; open: () => void }) {
  return (
    <button className="attention-row" onClick={open}>
      <span
        className={`attention-icon ${alert.status === "REVIEWED" ? "reviewed" : ""}`}
      >
        <Bell size={19} />
      </span>
      <div>
        <strong>{alert.title}</strong>
        <span>
          {alert.pharmacy_name} · {alert.source_label || alert.device_name}
        </span>
        <small>
          {when(alert.occurred_at)}
          {alert.historical ? " · Synced history" : ""}
        </small>
      </div>
      <div className="attention-end">
        <Badge value={alert.status} />
        <ArrowRight size={17} />
      </div>
    </button>
  );
}

export function Overview(
  props: WorkspaceProps & { navigate: (page: Page) => void },
) {
  const resource = useResource<Dashboard>(
    path("dashboard", props.scope),
    props.revision,
  );
  const [alertId, setAlertId] = useState<string | null>(null);
  const data = resource.data;
  if (!data)
    return resource.error ? (
      <ErrorNotice error={resource.error} retry={resource.refresh} />
    ) : (
      <Loading text="Getting your pharmacy overview…" />
    );
  const attention = data.alerts.filter((alert) => alert.status !== "REVIEWED");
  return (
    <>
      {Boolean(resource.error) && (
        <ErrorNotice error={resource.error} retry={resource.refresh} />
      )}
      <div className="overview-intro">
        <div>
          <span className="eyebrow">YOUR GROUP AT A GLANCE</span>
          <h1>Clarity for the day ahead.</h1>
          <p>Keep your pharmacies connected and your team informed.</p>
        </div>
        <div className="last-sync">
          <Clock3 size={15} />
          <span>
            Updated {ago(data.generated_at)}
            <small>{when(data.generated_at)} · Dublin time</small>
          </span>
        </div>
      </div>
      <div className="stat-grid">
        {[
          {
            name: "Pharmacies",
            value: data.summary.pharmacies,
            detail: "In your current access scope",
            icon: Building2,
            page: "pharmacies" as Page,
            tone: "teal",
          },
          {
            name: "Connected laptops",
            value: data.summary.connected_laptops,
            detail: `${data.summary.total_laptops} registered in this view`,
            icon: Laptop,
            page: "laptops" as Page,
            tone: "blue",
          },
          {
            name: "Open alerts",
            value: data.summary.open_alerts,
            detail: "Observations awaiting attention",
            icon: Bell,
            page: "alerts" as Page,
            tone: "amber",
          },
          {
            name: "Reviewed incidents",
            value: data.summary.reviewed_incidents,
            detail: "Cases created by staff review",
            icon: ClipboardCheck,
            page: "incidents" as Page,
            tone: "lavender",
          },
        ].map((stat) => (
          <button
            className="stat-card"
            key={stat.name}
            onClick={() => props.navigate(stat.page)}
          >
            <span className={`stat-icon ${stat.tone}`}>
              <stat.icon size={20} />
            </span>
            <span className="stat-name">{stat.name}</span>
            <strong>{stat.value}</strong>
            <small>{stat.detail}</small>
            <ArrowRight className="stat-arrow" size={17} />
          </button>
        ))}
      </div>
      {data.summary.pharmacies === 0 && (
        <div className="onboarding">
          <span className="onboarding-icon">
            <Building2 size={30} />
          </span>
          <div>
            <span className="eyebrow">BUILD YOUR WORKSPACE</span>
            <h3>
              {props.session.user.role === "OWNER"
                ? "Your first pharmacy starts here."
                : "Your workspace is ready for an assignment."}
            </h3>
            <p>
              {props.session.user.role === "OWNER"
                ? "Add a branch, invite your team, then connect an existing laptop. Your overview will fill with real reports as they arrive."
                : "Ask your workspace owner to give your account access to a pharmacy."}
            </p>
          </div>
          {props.session.user.role === "OWNER" && (
            <Button
              className="primary"
              onClick={() => props.navigate("pharmacies")}
            >
              Add first pharmacy
              <ArrowRight size={17} />
            </Button>
          )}
        </div>
      )}
      <div className="dashboard-columns">
        <section className="panel">
          <header className="panel-heading">
            <div>
              <span className="eyebrow">HUMAN REVIEW</span>
              <h3>Attention queue</h3>
            </div>
            <button
              className="text-button"
              onClick={() => props.navigate("alerts")}
            >
              View all
              <ArrowRight size={15} />
            </button>
          </header>
          {attention.length ? (
            <div>
              {attention.map((alert) => (
                <AlertRow
                  key={alert.id}
                  alert={alert}
                  open={() => setAlertId(alert.id)}
                />
              ))}
            </div>
          ) : (
            <Empty
              title={
                data.summary.total_laptops
                  ? "No recent alerts need review"
                  : "Your attention queue is empty"
              }
            >
              {data.summary.total_laptops
                ? "No outstanding observations appear in the recent reports. Open all alerts to check the complete available list."
                : "Reports will appear after an authorised laptop connects and sends observations."}
            </Empty>
          )}
          <footer className="panel-foot">
            <ShieldAlert size={15} />
            Observations need staff review. They are not findings of theft.
          </footer>
        </section>
        <section className="panel">
          <header className="panel-heading">
            <div>
              <span className="eyebrow">LAPTOP CONNECTIONS</span>
              <h3>Across your pharmacies</h3>
            </div>
            <button
              className="text-button"
              onClick={() => props.navigate("laptops")}
            >
              Manage
              <ArrowRight size={15} />
            </button>
          </header>
          {data.devices.length ? (
            <div>
              {data.devices.map((device) => (
                <div className="device-summary" key={device.id}>
                  <span className="record-icon">
                    <Laptop size={19} />
                  </span>
                  <div>
                    <strong>{device.name}</strong>
                    <small>{device.pharmacy_name}</small>
                    <Monitoring device={device} />
                  </div>
                  <div className="device-summary-end">
                    <Badge value={connectionStatus(device)} />
                    <small>{ago(device.last_seen_at)}</small>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <Empty title="No laptops connected yet">
              Connect an existing pharmacy laptop to receive its health reports
              here.
            </Empty>
          )}
          <footer className="panel-foot">
            <Link2 size={15} />
            Connection does not activate CCTV or arm speakers.
          </footer>
        </section>
      </div>
      <section className="panel recent-cases">
        <header className="panel-heading">
          <div>
            <span className="eyebrow">FOLLOW THROUGH</span>
            <h3>Recently reviewed incidents</h3>
          </div>
          <button
            className="text-button"
            onClick={() => props.navigate("incidents")}
          >
            View incidents
            <ArrowRight size={15} />
          </button>
        </header>
        {data.incidents.length ? (
          <Table
            rows={data.incidents}
            rowKey={(item) => item.id}
            columns={[
              {
                title: "Incident",
                cell: (item) => (
                  <div className="record-title">
                    <span className="record-icon">
                      <ClipboardCheck size={17} />
                    </span>
                    <strong>{item.title}</strong>
                  </div>
                ),
              },
              { title: "Pharmacy", cell: (item) => item.pharmacy_name },
              {
                title: "Staff classification",
                cell: (item) => <Badge value={item.classification} />,
              },
              {
                title: "Status",
                cell: (item) => <Badge value={item.status} />,
              },
              { title: "Reviewed", cell: (item) => when(item.reviewed_at) },
            ]}
          />
        ) : (
          <div className="inline-empty">
            <CheckCheck size={20} />
            <p>
              No reviewed cases yet. A case is created only when a staff member
              records a review.
            </p>
          </div>
        )}
      </section>
      {alertId && (
        <Drawer
          title="Review observation"
          subtitle="Record a clear, factual staff decision."
          close={() => setAlertId(null)}
        >
          <AlertDetail id={alertId} changed={props.changed} />
        </Drawer>
      )}
    </>
  );
}

function EnrolmentForm(props: WorkspaceProps & { close: () => void }) {
  const available = props.pharmacies.filter((pharmacy) => pharmacy.active);
  const [pharmacy, setPharmacy] = useState(
    props.scope || (available.length === 1 ? available[0].id : ""),
  );
  const [name, setName] = useState("");
  const [platform, setPlatform] = useState("");
  const [result, setResult] = useState<OneTimeToken | null>(null);
  const mutation = useMutation(props.changed);
  if (result)
    return (
      <>
        <CopyValue
          value={result.token}
          title="Laptop connection code"
          expires={result.expires_at}
        />
        <ol className="steps">
          <li>
            <a
              className="text-button"
              href="/downloads/cloud_companion.py"
              download
            >
              Download the macOS / Linux connection tool
            </a>{" "}
            on the intended laptop. It requires Python 3.
          </li>
          <li>
            Use the exact laptop name <strong>{name}</strong> on a{" "}
            <strong>{platform === "MACOS" ? "macOS" : "Linux"}</strong> laptop.
            Open a terminal in the download folder.
          </li>
          <li>
            Run the enrolment command below. Enter this code privately when
            prompted; it expires after ten minutes.
          </li>
          <li>
            Run the connection tool and keep that terminal open. Check this
            console for a fresh heartbeat.
          </li>
        </ol>
        <div className="command-panel">
          <small>Requires Python 3 on macOS or Linux.</small>
          <pre>
            <code>{`python3 cloud_companion.py enrol --server ${window.location.origin} --name "Exact laptop name"
python3 cloud_companion.py run`}</code>
          </pre>
          <p>
            Replace the name with the one shown above. Stop connection reports
            with Ctrl+C. The code is never part of the command or a URL.
          </p>
        </div>
        <div className="notice subtle">
          Connecting the laptop does not select a camera, start monitoring or
          arm a speaker. Complete those steps in its local AisleSignals
          interface.
        </div>
        <Button className="primary" onClick={props.close}>
          Done
        </Button>
      </>
    );
  return (
    <form
      onSubmit={mutation.submit(async (signal) =>
        setResult(
          await client.post<OneTimeToken>(
            "/devices/enrolments",
            { pharmacy_id: pharmacy, name, platform },
            { signal },
          ),
        ),
      )}
    >
      <Field label="Pharmacy">
        <select
          value={pharmacy}
          onChange={(event) => setPharmacy(event.target.value)}
          required
        >
          <option value="">Select a pharmacy</option>
          {available.map((item) => (
            <option value={item.id} key={item.id}>
              {item.name}
            </option>
          ))}
        </select>
      </Field>
      <Field
        label="Laptop name"
        hint="A short name staff will recognise, such as the workstation’s existing label."
      >
        <input
          required
          maxLength={120}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </Field>
      <Field label="Operating system">
        <select
          required
          value={platform}
          onChange={(event) => setPlatform(event.target.value)}
        >
          <option value="">Select its operating system</option>
          <option value="MACOS">macOS</option>
          <option value="WINDOWS">Windows</option>
          <option value="OTHER">Linux</option>
        </select>
      </Field>
      {platform === "WINDOWS" && (
        <div className="notice warning-notice">
          Windows pairing through this connection helper is not available yet.
          Its secure credential support still needs verification. The Windows
          local application and this browser console remain separate and
          available.
        </div>
      )}
      <p className="muted small">
        Only connect a laptop that your pharmacy is authorised to use.
        Connection does not certify its camera compatibility or performance.
      </p>
      {Boolean(mutation.error) && <ErrorNotice error={mutation.error} />}
      <div className="form-actions">
        <Button className="secondary" type="button" onClick={props.close}>
          Cancel
        </Button>
        <Button
          className="primary"
          type="submit"
          busy={mutation.busy}
          disabled={!available.length || platform === "WINDOWS"}
        >
          Create connection code
        </Button>
      </div>
    </form>
  );
}

function DeviceDetail({
  device,
  canManage,
  changed,
  close,
}: {
  device: Device;
  canManage: boolean;
  changed: () => void;
  close: () => void;
}) {
  const [confirm, setConfirm] = useState(false);
  const mutation = useMutation(() => {
    changed();
    close();
  });
  return (
    <>
      <dl>
        <Detail label="Pharmacy">{device.pharmacy_name}</Detail>
        <Detail label="Connection">
          <Badge value={connectionStatus(device)} />
        </Detail>
        <Detail label="Reported monitoring">
          <Monitoring device={device} />
        </Detail>
        <Detail label="Last heartbeat">
          {when(device.last_seen_at)} · Dublin time
        </Detail>
        <Detail label="Operating system">
          {device.platform === "MACOS" ? "macOS" : label(device.platform)}
        </Detail>
        <Detail label="Application version">
          {device.app_version || "Not reported"}
        </Detail>
        <Detail label="Reported camera count">
          {device.camera_count ?? "Unknown"}
        </Detail>
      </dl>
      <div className="notice subtle">
        A fresh connection report confirms that the laptop can contact this
        console. It does not prove camera quality, detection accuracy or speaker
        audibility.
      </div>
      {canManage && device.connection_status !== "REVOKED" && (
        <section className="danger-zone">
          <h3>Revoke this connection</h3>
          <p>
            This laptop will no longer be able to send reports with its current
            credential. Existing records remain available.
          </p>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={confirm}
              onChange={(event) => setConfirm(event.target.checked)}
            />
            <span>I intend to disconnect this laptop.</span>
          </label>
          {Boolean(mutation.error) && <ErrorNotice error={mutation.error} />}
          <Button
            className="danger-button"
            disabled={!confirm}
            busy={mutation.busy}
            onClick={() => {
              void mutation.run((signal) =>
                client.post(
                  `/devices/${encodeURIComponent(device.id)}/revoke`,
                  { expected_version: device.version },
                  { signal },
                ),
              );
            }}
          >
            <Unplug size={16} />
            Revoke connection
          </Button>
        </section>
      )}
    </>
  );
}

export function Laptops(props: WorkspaceProps) {
  const resource = useResource<Collection<Device>>(
    path("devices", props.scope),
    props.revision,
  );
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("ALL");
  const [selected, setSelected] = useState<Device | "new" | null>(null);
  const canManage = props.session.user.role !== "REVIEWER";
  const items = (resource.data?.items || []).filter(
    (item) =>
      (filter === "ALL" || item.connection_status === filter) &&
      `${item.name} ${item.pharmacy_name} ${item.platform}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  return (
    <>
      <div className="section-heading">
        <div>
          <h1>Connected where it matters</h1>
          <p>Connection and reported monitoring are separate health signals.</p>
        </div>
        {canManage && (
          <Button className="primary" onClick={() => setSelected("new")}>
            <Plus size={17} />
            Connect a laptop
          </Button>
        )}
      </div>
      <div className="notice subtle">
        <Laptop size={19} />
        <p>
          Manage connections here. Staff select CCTV and commission speakers in
          the local app on each pharmacy laptop.
        </p>
      </div>
      <section className="panel">
        <Toolbar search={search} setSearch={setSearch}>
          <select
            aria-label="Laptop connection filter"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          >
            <option value="ALL">All connections</option>
            {["ONLINE", "OFFLINE", "NEVER_CONNECTED", "REVOKED"].map(
              (status) => (
                <option value={status} key={status}>
                  {label(status)}
                </option>
              ),
            )}
          </select>
        </Toolbar>
        {Boolean(resource.error) && (
          <ErrorNotice error={resource.error} retry={resource.refresh} />
        )}
        {!resource.data && resource.loading ? (
          <Loading />
        ) : items.length ? (
          <Table
            rows={items}
            rowKey={(item) => item.id}
            onOpen={setSelected}
            columns={[
              {
                title: "Laptop",
                cell: (item) => (
                  <div className="record-title">
                    <span className="record-icon">
                      <Laptop size={19} />
                    </span>
                    <div>
                      <strong>{item.name}</strong>
                      <small>
                        {item.platform === "MACOS"
                          ? "macOS"
                          : label(item.platform)}{" "}
                        · {item.app_version || "Version unknown"}
                      </small>
                    </div>
                  </div>
                ),
              },
              { title: "Pharmacy", cell: (item) => item.pharmacy_name },
              {
                title: "Connection",
                cell: (item) => <Badge value={connectionStatus(item)} />,
              },
              {
                title: "Reported monitoring",
                cell: (item) => <Monitoring device={item} />,
              },
              {
                title: "Last seen",
                cell: (item) => (
                  <span title={when(item.last_seen_at)}>
                    {ago(item.last_seen_at)}
                  </span>
                ),
              },
            ]}
          />
        ) : (
          !resource.error && (
            <Empty
              title={
                search || filter !== "ALL"
                  ? "No matching laptops"
                  : "Connect your first laptop"
              }
            >
              {search || filter !== "ALL"
                ? "Try another name or connection filter."
                : "Create a one-use connection code and enrol an existing pharmacy laptop. No connection is assumed before its own report arrives."}
            </Empty>
          )
        )}
      </section>
      {selected && (
        <Drawer
          title={
            selected === "new" ? "Connect a pharmacy laptop" : selected.name
          }
          close={() => setSelected(null)}
        >
          {selected === "new" ? (
            <EnrolmentForm {...props} close={() => setSelected(null)} />
          ) : (
            <DeviceDetail
              device={selected}
              canManage={canManage}
              changed={props.changed}
              close={() => setSelected(null)}
            />
          )}
        </Drawer>
      )}
    </>
  );
}

function AlertDetail({ id, changed }: { id: string; changed: () => void }) {
  const resource = useResource<Alert>(`/alerts/${encodeURIComponent(id)}`);
  const [outcome, setOutcome] = useState<Outcome>("UNCLEAR");
  const [note, setNote] = useState("");
  const [createIncident, setCreateIncident] = useState(false);
  const [title, setTitle] = useState("");
  const mutation = useMutation(() => {
    changed();
    resource.refresh();
  });
  const alert = resource.data;
  if (!alert)
    return resource.error ? (
      <ErrorNotice error={resource.error} retry={resource.refresh} />
    ) : (
      <Loading text="Loading the latest observation…" />
    );
  return (
    <>
      {Boolean(resource.error) && (
        <ErrorNotice error={resource.error} retry={resource.refresh} />
      )}
      <h3 className="detail-title">{alert.title}</h3>
      <div className="detail-badges">
        <Badge value={alert.status} />
        {alert.historical && (
          <span className="badge neutral">
            <Clock3 size={12} />
            Synced history
          </span>
        )}
      </div>
      <dl>
        <Detail label="Pharmacy">{alert.pharmacy_name}</Detail>
        <Detail label="Laptop / source">
          {alert.device_name} · {alert.source_label || "Unspecified source"}
        </Detail>
        <Detail label="Occurred">{when(alert.occurred_at)}</Detail>
        <Detail label="Received by console">{when(alert.received_at)}</Detail>
        <Detail label="Observation type">{label(alert.event_code)}</Detail>
      </dl>
      <p className="muted small">
        Times shown in Europe/Dublin. This console holds metadata only. Review
        authorised evidence and circumstances before recording an outcome.
      </p>
      {alert.review ? (
        <section className="review-complete">
          <span className="inline-icon">
            <CheckCheck size={20} />
            <strong>Staff review recorded</strong>
          </span>
          <Badge value={alert.review.outcome} />
          <p className="preserve-lines">{alert.review.note}</p>
          <small>
            Reviewed {when(alert.review.at)}
            {alert.incident_id
              ? " · Linked incident created"
              : " · No case created"}
          </small>
        </section>
      ) : (
        <>
          {alert.status === "OPEN" && (
            <Button
              className="secondary"
              busy={mutation.busy}
              onClick={() => {
                void mutation.run((signal) =>
                  client.post(
                    `/alerts/${encodeURIComponent(id)}/acknowledge`,
                    { expected_version: alert.version },
                    { signal },
                  ),
                );
              }}
            >
              <Check size={16} />
              Acknowledge attention
            </Button>
          )}
          <form
            className="review-form"
            onSubmit={mutation.submit((signal) =>
              client.post(
                `/alerts/${encodeURIComponent(id)}/review`,
                {
                  expected_version: alert.version,
                  outcome,
                  note,
                  create_incident: createIncident,
                  ...(createIncident ? { title: title || alert.title } : {}),
                },
                { signal },
              ),
            )}
          >
            <h3>Record your review</h3>
            <Field label="Staff outcome">
              <select
                value={outcome}
                onChange={(event) => setOutcome(event.target.value as Outcome)}
              >
                <option value="UNCLEAR">Unclear / insufficient evidence</option>
                <option value="NORMAL_SHOPPING">Normal shopping</option>
                <option value="SUSPECTED_INCIDENT">Suspected incident</option>
              </select>
            </Field>
            <Field
              label="Review notes"
              hint="Describe observed facts and your decision. Avoid patient data or unnecessary personal details."
            >
              <textarea
                value={note}
                onChange={(event) => setNote(event.target.value)}
                required
                maxLength={2000}
                rows={5}
              />
            </Field>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={createIncident}
                onChange={(event) => setCreateIncident(event.target.checked)}
              />
              <span>Create an incident for staff follow-up</span>
            </label>
            {createIncident && (
              <Field label="Incident title">
                <input
                  value={title}
                  placeholder={alert.title}
                  onChange={(event) => setTitle(event.target.value)}
                  maxLength={120}
                />
              </Field>
            )}
            <Button
              className="primary"
              type="submit"
              busy={mutation.busy}
              disabled={!note.trim()}
            >
              <ClipboardCheck size={16} />
              Save review
            </Button>
          </form>
        </>
      )}
      {Boolean(mutation.error) && <ErrorNotice error={mutation.error} />}
    </>
  );
}

export function Alerts(props: WorkspaceProps) {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("ALL");
  const [selected, setSelected] = useState<string | null>(null);
  const resource = useResource<Collection<Alert>>(
    path("alerts", props.scope, filter),
    props.revision,
  );
  const items = (resource.data?.items || []).filter((item) =>
    `${item.title} ${item.pharmacy_name} ${item.source_label} ${item.device_name}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  return (
    <>
      <div className="section-heading">
        <div>
          <h1>Every observation, a clear next step</h1>
          <p>
            Acknowledge reports, review the circumstances and record your
            decision.
          </p>
        </div>
      </div>
      <section className="panel">
        <Toolbar search={search} setSearch={setSearch}>
          <select
            aria-label="Alert status filter"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          >
            <option value="ALL">All statuses</option>
            <option value="OPEN">Open</option>
            <option value="ACKNOWLEDGED">Acknowledged</option>
            <option value="REVIEWED">Reviewed</option>
          </select>
        </Toolbar>
        {Boolean(resource.error) && (
          <ErrorNotice error={resource.error} retry={resource.refresh} />
        )}
        {!resource.data && resource.loading ? (
          <Loading />
        ) : items.length ? (
          <Table
            rows={items}
            rowKey={(item) => item.id}
            onOpen={(item) => setSelected(item.id)}
            columns={[
              {
                title: "Observation",
                cell: (item) => (
                  <div>
                    <strong>{item.title}</strong>
                    <small>
                      {item.source_label || item.device_name}
                      {item.historical
                        ? " · Synced history"
                        : " · Current report"}
                    </small>
                  </div>
                ),
              },
              { title: "Pharmacy", cell: (item) => item.pharmacy_name },
              { title: "Occurred", cell: (item) => when(item.occurred_at) },
              {
                title: "Status",
                cell: (item) => <Badge value={item.status} />,
              },
              {
                title: "Review outcome",
                cell: (item) =>
                  item.review ? (
                    <Badge value={item.review.outcome} />
                  ) : (
                    <span className="muted">Awaiting review</span>
                  ),
              },
            ]}
          />
        ) : (
          !resource.error && (
            <Empty
              title={
                search || filter !== "ALL"
                  ? "No matching observations"
                  : "No alerts have arrived"
              }
            >
              {search || filter !== "ALL"
                ? "Try another search or status filter."
                : "Authorised connected laptops can send observations here. An empty list does not certify that a pharmacy is monitored."}
            </Empty>
          )
        )}
        <footer className="panel-foot">
          <ShieldAlert size={15} />
          Newest 200 records · Staff review is required · Times shown in Dublin
        </footer>
      </section>
      {selected && (
        <Drawer title="Review observation" close={() => setSelected(null)}>
          <AlertDetail id={selected} changed={props.changed} />
        </Drawer>
      )}
    </>
  );
}

function IncidentForm({
  item,
  changed,
  close,
}: {
  item: Incident;
  changed: () => void;
  close: () => void;
}) {
  const [notes, setNotes] = useState(item.notes);
  const [status, setStatus] = useState(item.status);
  const mutation = useMutation(() => {
    changed();
    close();
  });
  return (
    <form
      onSubmit={mutation.submit((signal) =>
        client.patch(
          `/incidents/${encodeURIComponent(item.id)}`,
          { expected_version: item.version, status, notes },
          { signal },
        ),
      )}
    >
      <dl>
        <Detail label="Pharmacy">{item.pharmacy_name}</Detail>
        <Detail label="Staff classification">
          <Badge value={item.classification} />
        </Detail>
        <Detail label="Reviewed">{when(item.reviewed_at)}</Detail>
        <Detail label="Original observation">{item.alert_id}</Detail>
      </dl>
      <Field label="Case status">
        <select
          value={status}
          onChange={(event) =>
            setStatus(event.target.value as Incident["status"])
          }
        >
          <option value="OPEN">Open — follow-up needed</option>
          <option value="CLOSED">Closed</option>
        </select>
      </Field>
      <Field
        label="Case notes"
        hint="Keep notes factual. Changes are recorded in the server audit history."
      >
        <textarea
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
          rows={7}
          maxLength={4000}
          required
        />
      </Field>
      {Boolean(mutation.error) && <ErrorNotice error={mutation.error} />}
      <div className="form-actions">
        <Button className="secondary" type="button" onClick={close}>
          Cancel
        </Button>
        <Button className="primary" type="submit" busy={mutation.busy}>
          Save case
        </Button>
      </div>
    </form>
  );
}
function IncidentDetail({
  id,
  changed,
  close,
}: {
  id: string;
  changed: () => void;
  close: () => void;
}) {
  const resource = useResource<Incident>(
    `/incidents/${encodeURIComponent(id)}`,
  );
  if (!resource.data)
    return resource.error ? (
      <ErrorNotice error={resource.error} retry={resource.refresh} />
    ) : (
      <Loading />
    );
  return (
    <>
      <h3>{resource.data.title}</h3>
      <IncidentForm
        key={resource.data.version}
        item={resource.data}
        changed={changed}
        close={close}
      />
    </>
  );
}
export function Incidents(props: WorkspaceProps) {
  const resource = useResource<Collection<Incident>>(
    path("incidents", props.scope),
    props.revision,
  );
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("ALL");
  const [selected, setSelected] = useState<string | null>(null);
  const items = (resource.data?.items || []).filter(
    (item) =>
      (filter === "ALL" || item.status === filter) &&
      `${item.title} ${item.pharmacy_name} ${item.notes}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  return (
    <>
      <div className="section-heading">
        <div>
          <h1>Reviewed. Recorded. Followed up.</h1>
          <p>Cases created by your team after reviewing an observation.</p>
        </div>
      </div>
      <section className="panel">
        <Toolbar search={search} setSearch={setSearch}>
          <select
            aria-label="Incident status filter"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          >
            <option value="ALL">All cases</option>
            <option value="OPEN">Open</option>
            <option value="CLOSED">Closed</option>
          </select>
        </Toolbar>
        {Boolean(resource.error) && (
          <ErrorNotice error={resource.error} retry={resource.refresh} />
        )}
        {!resource.data && resource.loading ? (
          <Loading />
        ) : items.length ? (
          <Table
            rows={items}
            rowKey={(item) => item.id}
            onOpen={(item) => setSelected(item.id)}
            columns={[
              {
                title: "Incident",
                cell: (item) => (
                  <div className="record-title">
                    <span className="record-icon">
                      <ClipboardCheck size={18} />
                    </span>
                    <strong>{item.title}</strong>
                  </div>
                ),
              },
              { title: "Pharmacy", cell: (item) => item.pharmacy_name },
              {
                title: "Staff classification",
                cell: (item) => <Badge value={item.classification} />,
              },
              {
                title: "Status",
                cell: (item) => <Badge value={item.status} />,
              },
              { title: "Reviewed", cell: (item) => when(item.reviewed_at) },
            ]}
          />
        ) : (
          !resource.error && (
            <Empty
              title={
                search || filter !== "ALL"
                  ? "No matching incidents"
                  : "No reviewed incidents yet"
              }
            >
              {search || filter !== "ALL"
                ? "Try a different search or case status."
                : "Review an observation in Alerts and choose to create a case when staff follow-up is needed."}
            </Empty>
          )
        )}
        <footer className="panel-foot">
          <ClipboardCheck size={15} />
          Newest 200 records · Human classifications are not legal findings
        </footer>
      </section>
      {selected && (
        <Drawer title="Incident follow-up" close={() => setSelected(null)}>
          <IncidentDetail
            id={selected}
            changed={props.changed}
            close={() => setSelected(null)}
          />
        </Drawer>
      )}
    </>
  );
}
