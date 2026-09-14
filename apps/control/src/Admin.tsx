import { useState } from "react";
import { Building2, Check, MailPlus, Plus, ShieldCheck } from "lucide-react";
import { client } from "./api";
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
  label,
  useMutation,
  useResource,
  when,
} from "./ui";
import type {
  Collection,
  OneTimeToken,
  Pharmacy,
  Role,
  Session,
  User,
} from "./types";

export type WorkspaceProps = {
  session: Session;
  pharmacies: Pharmacy[];
  revision: number;
  changed: () => void;
  scope: string;
};

function PharmacyForm({
  value,
  close,
  changed,
}: {
  value: Pharmacy | null;
  close: () => void;
  changed: () => void;
}) {
  const [name, setName] = useState(value?.name || "");
  const [address, setAddress] = useState(value?.address || "");
  const [active, setActive] = useState(value?.active ?? true);
  const mutation = useMutation(() => {
    changed();
    close();
  });
  return (
    <form
      onSubmit={mutation.submit((signal) =>
        value
          ? client.patch(
              `/pharmacies/${encodeURIComponent(value.id)}`,
              { expected_version: value.version, name, address, active },
              { signal },
            )
          : client.post(
              "/pharmacies",
              { name, address, timezone: "Europe/Dublin" },
              { signal },
            ),
      )}
    >
      <div className="notice subtle">
        <Building2 size={19} />
        <p>
          Register a pharmacy so you can assign staff and connect its laptops.
          Registration does not activate monitoring or create a charge.
        </p>
      </div>
      <Field label="Pharmacy name">
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
          maxLength={120}
        />
      </Field>
      <Field
        label="Address"
        hint="Use the branch’s postal address. You can complete this later."
      >
        <textarea
          value={address}
          onChange={(event) => setAddress(event.target.value)}
          maxLength={500}
          rows={3}
        />
      </Field>
      <Field label="Timezone">
        <input value="Europe/Dublin" readOnly />
      </Field>
      {value && (
        <label className="checkbox">
          <input
            type="checkbox"
            checked={active}
            onChange={(event) => setActive(event.target.checked)}
          />
          <span>Pharmacy enabled in this workspace</span>
        </label>
      )}
      {Boolean(mutation.error) && <ErrorNotice error={mutation.error} />}
      <div className="form-actions">
        <Button className="secondary" type="button" onClick={close}>
          Cancel
        </Button>
        <Button className="primary" busy={mutation.busy} type="submit">
          <Check size={16} />
          {value ? "Save changes" : "Add pharmacy"}
        </Button>
      </div>
    </form>
  );
}

export function Pharmacies(props: WorkspaceProps) {
  const resource = useResource<Collection<Pharmacy>>(
    "/pharmacies",
    props.revision,
  );
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("ALL");
  const [selected, setSelected] = useState<Pharmacy | "new" | null>(null);
  const owner = props.session.user.role === "OWNER";
  const items = (resource.data?.items || []).filter(
    (item) =>
      (!props.scope || item.id === props.scope) &&
      (filter === "ALL" || item.active === (filter === "ACTIVE")) &&
      `${item.name} ${item.address}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  return (
    <>
      <div className="section-heading">
        <div>
          <h1>Your pharmacy network</h1>
          <p>Branch details and access, managed in one place.</p>
        </div>
        {owner && (
          <Button className="primary" onClick={() => setSelected("new")}>
            <Plus size={17} />
            Add pharmacy
          </Button>
        )}
      </div>
      <section className="panel">
        <Toolbar search={search} setSearch={setSearch}>
          <select
            aria-label="Pharmacy status"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          >
            <option value="ALL">All statuses</option>
            <option value="ACTIVE">Enabled</option>
            <option value="INACTIVE">Disabled</option>
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
                title: "Pharmacy",
                cell: (item) => (
                  <div className="record-title">
                    <span className="record-icon">
                      <Building2 size={18} />
                    </span>
                    <div>
                      <strong>{item.name}</strong>
                      <small>{item.address || "Address not yet added"}</small>
                    </div>
                  </div>
                ),
              },
              {
                title: "Status",
                cell: (item) => (
                  <Badge value={item.active ? "ACTIVE" : "INACTIVE"} />
                ),
              },
              { title: "Timezone", cell: (item) => item.timezone },
              { title: "Registered", cell: (item) => when(item.created_at) },
            ]}
          />
        ) : (
          !resource.error && (
            <Empty
              title={
                search || filter !== "ALL"
                  ? "No matching pharmacies"
                  : "Start with your first pharmacy"
              }
            >
              {search || filter !== "ALL"
                ? "Try a different search or status filter."
                : owner
                  ? "Add your first branch, then invite its team and connect a laptop."
                  : "Your workspace owner can assign pharmacies to your account."}
            </Empty>
          )
        )}
      </section>
      {selected && (
        <Drawer
          title={selected === "new" ? "Add a pharmacy" : selected.name}
          close={() => setSelected(null)}
        >
          {owner ? (
            <PharmacyForm
              value={selected === "new" ? null : selected}
              close={() => setSelected(null)}
              changed={props.changed}
            />
          ) : (
            selected !== "new" && (
              <dl>
                <Detail label="Address">
                  {selected.address || "Not yet added"}
                </Detail>
                <Detail label="Timezone">{selected.timezone}</Detail>
                <Detail label="Status">
                  <Badge value={selected.active ? "ACTIVE" : "INACTIVE"} />
                </Detail>
              </dl>
            )
          )}
        </Drawer>
      )}
    </>
  );
}

function UserForm({
  value,
  currentUserId,
  pharmacies,
  changed,
  close,
}: {
  value: User | null;
  currentUserId: string;
  pharmacies: Pharmacy[];
  changed: () => void;
  close: () => void;
}) {
  const [name, setName] = useState(value?.name || "");
  const [email, setEmail] = useState(value?.email || "");
  const [role, setRole] = useState<Role>(value?.role || "REVIEWER");
  const [active, setActive] = useState(value?.active ?? true);
  const [ids, setIds] = useState(value?.pharmacy_ids || []);
  const [invitation, setInvitation] = useState<OneTimeToken | null>(null);
  const mutation = useMutation(() => {
    if (value?.id === currentUserId) {
      client.reset();
      client.onUnauthorised?.();
      return;
    }
    changed();
    if (value) close();
  });
  if (invitation)
    return (
      <>
        <CopyValue
          value={`${window.location.origin}/#accept-invite?token=${encodeURIComponent(invitation.token)}`}
          title="Private invitation link"
          expires={invitation.expires_at}
        />
        <div className="notice subtle">
          <MailPlus size={19} />
          <p>
            No email has been sent. Share this link privately with {email}.
            They’ll create their own password and set up an authenticator.
          </p>
        </div>
        <Button className="primary" onClick={close}>
          Done
        </Button>
      </>
    );
  const invalidBranches = role !== "OWNER" && !ids.length;
  return (
    <form
      onSubmit={mutation.submit(async (signal) => {
        const pharmacy_ids = role === "OWNER" ? [] : ids;
        if (value)
          return client.patch(
            `/users/${encodeURIComponent(value.id)}`,
            { expected_version: value.version, active, role, pharmacy_ids },
            { signal },
          );
        const result = await client.post<OneTimeToken>(
          "/invitations",
          { name, email, role, pharmacy_ids },
          { signal },
        );
        setInvitation(result);
      })}
    >
      <Field label="Full name">
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
          maxLength={120}
          readOnly={Boolean(value)}
          autoComplete="off"
        />
      </Field>
      <Field label="Email address">
        <input
          type="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          required
          maxLength={254}
          readOnly={Boolean(value)}
          autoComplete="off"
        />
      </Field>
      <Field
        label="Workspace role"
        hint={
          role === "OWNER"
            ? "Owners manage the whole group, users and all pharmacies."
            : role === "MANAGER"
              ? "Managers review records and manage laptops at assigned pharmacies."
              : "Reviewers work with alerts and incidents at assigned pharmacies."
        }
      >
        <select
          value={role}
          onChange={(event) => setRole(event.target.value as Role)}
        >
          <option value="REVIEWER">Reviewer</option>
          <option value="MANAGER">Manager</option>
          <option value="OWNER">Owner</option>
        </select>
      </Field>
      {role !== "OWNER" && (
        <fieldset className="assignment">
          <legend>Assigned pharmacies</legend>
          {pharmacies
            .filter((pharmacy) => pharmacy.active || ids.includes(pharmacy.id))
            .map((pharmacy) => (
              <label className="checkbox" key={pharmacy.id}>
                <input
                  type="checkbox"
                  checked={ids.includes(pharmacy.id)}
                  onChange={(event) =>
                    setIds((current) =>
                      event.target.checked
                        ? [...current, pharmacy.id]
                        : current.filter((id) => id !== pharmacy.id),
                    )
                  }
                />
                <span>
                  {pharmacy.name}
                  {!pharmacy.active && " (disabled)"}
                </span>
              </label>
            ))}
          {!pharmacies.length && (
            <p>Add a pharmacy before inviting branch staff.</p>
          )}
          {invalidBranches && <small>Select at least one pharmacy.</small>}
        </fieldset>
      )}
      {value && (
        <>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={active}
              onChange={(event) => setActive(event.target.checked)}
            />
            <span>Account enabled</span>
          </label>
          <p className="muted small">
            Changing access signs this person out of existing sessions. Keep at
            least one active owner.
          </p>
        </>
      )}
      {Boolean(mutation.error) && <ErrorNotice error={mutation.error} />}
      <div className="form-actions">
        <Button type="button" className="secondary" onClick={close}>
          Cancel
        </Button>
        <Button
          className="primary"
          type="submit"
          busy={mutation.busy}
          disabled={invalidBranches}
        >
          {value ? "Save access" : "Create invitation"}
        </Button>
      </div>
    </form>
  );
}

export function Users(props: WorkspaceProps) {
  const resource = useResource<Collection<User>>("/users", props.revision);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("ALL");
  const [selected, setSelected] = useState<User | "new" | null>(null);
  const items = (resource.data?.items || []).filter(
    (item) =>
      (!props.scope ||
        item.role === "OWNER" ||
        item.pharmacy_ids.includes(props.scope)) &&
      (filter === "ALL" || item.role === filter) &&
      `${item.name} ${item.email}`.toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <>
      <div className="section-heading">
        <div>
          <h1>A team with the right access</h1>
          <p>
            Named accounts, clear responsibilities and authenticator protection.
          </p>
        </div>
        <Button className="primary" onClick={() => setSelected("new")}>
          <MailPlus size={17} />
          Invite team member
        </Button>
      </div>
      <section className="panel">
        <Toolbar search={search} setSearch={setSearch}>
          <select
            aria-label="Filter role"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          >
            <option value="ALL">All roles</option>
            <option value="OWNER">Owners</option>
            <option value="MANAGER">Managers</option>
            <option value="REVIEWER">Reviewers</option>
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
                title: "Team member",
                cell: (item) => (
                  <div className="record-title">
                    <span className="avatar">
                      {item.name.slice(0, 1).toUpperCase()}
                    </span>
                    <div>
                      <strong>{item.name}</strong>
                      <small>{item.email}</small>
                    </div>
                  </div>
                ),
              },
              {
                title: "Role",
                cell: (item) => (
                  <span className="inline-icon">
                    <ShieldCheck size={15} />
                    {label(item.role)}
                  </span>
                ),
              },
              {
                title: "Pharmacy access",
                cell: (item) =>
                  item.role === "OWNER"
                    ? "All pharmacies"
                    : item.pharmacy_ids
                        .map(
                          (id) =>
                            props.pharmacies.find(
                              (pharmacy) => pharmacy.id === id,
                            )?.name || "Assigned pharmacy",
                        )
                        .join(", "),
              },
              {
                title: "Account",
                cell: (item) => (
                  <Badge value={item.active ? "ACTIVE" : "DISABLED"} />
                ),
              },
            ]}
          />
        ) : (
          !resource.error && (
            <Empty title="No matching team members">
              Try another name, email or role. Invitations appear here after
              they are accepted.
            </Empty>
          )
        )}
      </section>
      {selected && (
        <Drawer
          title={
            selected === "new"
              ? "Invite a team member"
              : "Manage account access"
          }
          close={() => setSelected(null)}
        >
          <UserForm
            value={selected === "new" ? null : selected}
            currentUserId={props.session.user.id}
            pharmacies={props.pharmacies}
            changed={props.changed}
            close={() => setSelected(null)}
          />
        </Drawer>
      )}
    </>
  );
}
