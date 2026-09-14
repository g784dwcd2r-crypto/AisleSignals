CREATE UNIQUE INDEX pharmacies_tenant_identity ON aislesignals_control.pharmacies(organisation_id, id);

CREATE TABLE aislesignals_control.device_enrolments (
    id uuid PRIMARY KEY,
    organisation_id uuid NOT NULL,
    pharmacy_id uuid NOT NULL,
    name varchar(100) NOT NULL,
    platform varchar(12) NOT NULL CHECK (platform IN ('MACOS','WINDOWS','OTHER')),
    token_hash char(64) NOT NULL UNIQUE,
    created_by uuid NOT NULL REFERENCES aislesignals_control.users(id),
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    FOREIGN KEY (organisation_id, pharmacy_id) REFERENCES aislesignals_control.pharmacies(organisation_id,id)
);

CREATE TABLE aislesignals_control.devices (
    id uuid PRIMARY KEY,
    organisation_id uuid NOT NULL,
    pharmacy_id uuid NOT NULL,
    name varchar(100) NOT NULL,
    platform varchar(12) NOT NULL CHECK (platform IN ('MACOS','WINDOWS','OTHER')),
    credential_hash char(64) NOT NULL UNIQUE,
    app_version varchar(64) NOT NULL,
    last_seen_at timestamptz,
    heartbeat_sequence bigint NOT NULL DEFAULT -1,
    heartbeat_hash char(64),
    monitoring_status varchar(12) NOT NULL DEFAULT 'UNKNOWN' CHECK (monitoring_status IN ('ACTIVE','STOPPED','DEGRADED','UNKNOWN')),
    camera_count integer NOT NULL DEFAULT 0 CHECK (camera_count BETWEEN 0 AND 64),
    revoked_at timestamptz,
    version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (organisation_id, pharmacy_id, id),
    FOREIGN KEY (organisation_id, pharmacy_id) REFERENCES aislesignals_control.pharmacies(organisation_id,id)
);

CREATE TABLE aislesignals_control.alerts (
    id uuid PRIMARY KEY,
    organisation_id uuid NOT NULL,
    pharmacy_id uuid NOT NULL,
    device_id uuid NOT NULL,
    source_event_id uuid NOT NULL,
    payload_hash char(64) NOT NULL,
    event_code varchar(40) NOT NULL,
    title varchar(120) NOT NULL,
    source_label varchar(120) NOT NULL,
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    historical boolean NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','ACKNOWLEDGED','REVIEWED')),
    version integer NOT NULL DEFAULT 1,
    review_outcome varchar(24) CHECK (review_outcome IN ('NORMAL_SHOPPING','UNCLEAR','SUSPECTED_INCIDENT')),
    review_note varchar(2000),
    reviewed_by varchar(120),
    reviewed_at timestamptz,
    UNIQUE (device_id,source_event_id),
    UNIQUE (organisation_id,pharmacy_id,id),
    FOREIGN KEY (organisation_id,pharmacy_id,device_id) REFERENCES aislesignals_control.devices(organisation_id,pharmacy_id,id)
);

CREATE INDEX alert_scope_time ON aislesignals_control.alerts(organisation_id,pharmacy_id,received_at DESC);

CREATE TABLE aislesignals_control.incidents (
    id uuid PRIMARY KEY,
    organisation_id uuid NOT NULL,
    pharmacy_id uuid NOT NULL,
    alert_id uuid NOT NULL UNIQUE,
    title varchar(160) NOT NULL,
    classification varchar(24) NOT NULL CHECK (classification IN ('NORMAL_SHOPPING','UNCLEAR','SUSPECTED_INCIDENT')),
    status varchar(12) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','CLOSED')),
    notes varchar(4000) NOT NULL,
    reviewed_by varchar(120) NOT NULL,
    reviewed_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    version integer NOT NULL DEFAULT 1,
    FOREIGN KEY (organisation_id,pharmacy_id,alert_id) REFERENCES aislesignals_control.alerts(organisation_id,pharmacy_id,id)
);

CREATE INDEX incident_scope_time ON aislesignals_control.incidents(organisation_id,pharmacy_id,created_at DESC);
