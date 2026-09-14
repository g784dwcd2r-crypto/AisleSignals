CREATE TABLE aislesignals_control.evidence_objects (
    id uuid PRIMARY KEY,
    organisation_id uuid NOT NULL,
    pharmacy_id uuid NOT NULL,
    device_id uuid NOT NULL,
    alert_id uuid NOT NULL,
    source_event_id uuid NOT NULL,
    kind varchar(20) NOT NULL CHECK (kind IN ('OVERVIEW','INTERACTION_CROP','CLIP')),
    content_type varchar(24) NOT NULL CHECK (content_type IN ('image/jpeg','video/mp4','video/webm')),
    expected_bytes integer NOT NULL CHECK (expected_bytes BETWEEN 1 AND 8388608),
    sha256 char(64) NOT NULL,
    duration_ms integer CHECK (duration_ms BETWEEN 1 AND 20000),
    manifest_hash char(64) NOT NULL,
    state varchar(12) NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING','AVAILABLE','REVOKED','DELETED')),
    object_key varchar(100) NOT NULL UNIQUE,
    kek_version varchar(32) NOT NULL,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    uploaded_at timestamptz,
    revoked_at timestamptz,
    deleted_at timestamptz,
    UNIQUE (organisation_id, pharmacy_id, id),
    UNIQUE (device_id, source_event_id, kind),
    FOREIGN KEY (organisation_id, pharmacy_id, device_id)
        REFERENCES aislesignals_control.devices(organisation_id, pharmacy_id, id),
    FOREIGN KEY (organisation_id, pharmacy_id, alert_id)
        REFERENCES aislesignals_control.alerts(organisation_id, pharmacy_id, id) ON DELETE CASCADE,
    CHECK ((kind = 'CLIP') = (duration_ms IS NOT NULL)),
    CHECK ((kind = 'CLIP') = (content_type IN ('video/mp4','video/webm'))),
    CHECK (kind = 'CLIP' OR (content_type = 'image/jpeg' AND expected_bytes <= 358400)),
    CHECK (state <> 'AVAILABLE' OR uploaded_at IS NOT NULL),
    CHECK (state <> 'REVOKED' OR revoked_at IS NOT NULL),
    CHECK (state <> 'DELETED' OR deleted_at IS NOT NULL)
);

CREATE INDEX evidence_alert_state ON aislesignals_control.evidence_objects
    (organisation_id, pharmacy_id, alert_id, state, created_at);
CREATE INDEX evidence_cleanup ON aislesignals_control.evidence_objects
    (device_id, state, expires_at, created_at);
