-- Cloud-only identity and pharmacy scope. No local data, defaults or seeded users.
CREATE TABLE aislesignals_control.organisations (
    id uuid PRIMARY KEY,
    name varchar(120) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE aislesignals_control.pharmacies (
    id uuid PRIMARY KEY,
    organisation_id uuid NOT NULL REFERENCES aislesignals_control.organisations(id),
    name varchar(120) NOT NULL,
    address varchar(500) NOT NULL DEFAULT '',
    timezone varchar(80) NOT NULL DEFAULT 'Europe/Dublin',
    active boolean NOT NULL DEFAULT true,
    version integer NOT NULL DEFAULT 1 CHECK(version > 0),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(id, organisation_id)
);
CREATE UNIQUE INDEX pharmacies_unique_name ON aislesignals_control.pharmacies (organisation_id, lower(name));
CREATE TABLE aislesignals_control.users (
    id uuid PRIMARY KEY,
    organisation_id uuid NOT NULL REFERENCES aislesignals_control.organisations(id),
    name varchar(120) NOT NULL,
    email varchar(254) NOT NULL UNIQUE CHECK(email = lower(email)),
    role varchar(10) NOT NULL CHECK(role IN ('OWNER', 'MANAGER', 'REVIEWER')),
    active boolean NOT NULL DEFAULT true,
    password_hash text NOT NULL,
    totp_encrypted text NOT NULL,
    last_totp_counter bigint NOT NULL,
    version integer NOT NULL DEFAULT 1 CHECK(version > 0),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(id, organisation_id)
);
CREATE TABLE aislesignals_control.user_pharmacies (
    user_id uuid NOT NULL,
    pharmacy_id uuid NOT NULL,
    organisation_id uuid NOT NULL,
    PRIMARY KEY(user_id, pharmacy_id),
    FOREIGN KEY(user_id, organisation_id) REFERENCES aislesignals_control.users(id, organisation_id),
    FOREIGN KEY(pharmacy_id, organisation_id) REFERENCES aislesignals_control.pharmacies(id, organisation_id)
);
CREATE TABLE aislesignals_control.sessions (
    id uuid PRIMARY KEY,
    token_hash char(64) NOT NULL UNIQUE,
    user_id uuid NOT NULL REFERENCES aislesignals_control.users(id),
    expires_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX sessions_user ON aislesignals_control.sessions(user_id);
CREATE TABLE aislesignals_control.invitations (
    id uuid PRIMARY KEY,
    organisation_id uuid NOT NULL REFERENCES aislesignals_control.organisations(id),
    created_by uuid NOT NULL REFERENCES aislesignals_control.users(id),
    token_hash char(64) NOT NULL UNIQUE,
    email varchar(254) NOT NULL,
    name varchar(120) NOT NULL,
    role varchar(10) NOT NULL CHECK(role IN ('OWNER', 'MANAGER', 'REVIEWER')),
    pharmacy_ids uuid[] NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE aislesignals_control.auth_challenges (
    id uuid PRIMARY KEY,
    token_hash char(64) NOT NULL UNIQUE,
    kind varchar(12) NOT NULL CHECK(kind IN ('SETUP', 'LOGIN', 'INVITATION')),
    user_id uuid REFERENCES aislesignals_control.users(id),
    invitation_id uuid REFERENCES aislesignals_control.invitations(id),
    payload_encrypted text,
    user_version integer,
    attempts integer NOT NULL DEFAULT 0,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE aislesignals_control.auth_attempts (
    bucket char(64) PRIMARY KEY,
    count integer NOT NULL CHECK(count > 0),
    window_start timestamptz NOT NULL,
    expires_at timestamptz NOT NULL
);
CREATE INDEX auth_attempts_expiry ON aislesignals_control.auth_attempts(expires_at);
CREATE TABLE aislesignals_control.audit_entries (
    id uuid PRIMARY KEY,
    organisation_id uuid NOT NULL REFERENCES aislesignals_control.organisations(id),
    actor_user_id uuid REFERENCES aislesignals_control.users(id),
    action varchar(80) NOT NULL,
    subject_id uuid,
    pharmacy_id uuid REFERENCES aislesignals_control.pharmacies(id),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
