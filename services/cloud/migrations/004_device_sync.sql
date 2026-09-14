ALTER TABLE aislesignals_control.alerts
    ADD COLUMN source_expires_at timestamptz,
    ADD COLUMN source_withdrawn_at timestamptz,
    ADD COLUMN source_purged boolean NOT NULL DEFAULT false,
    ADD COLUMN timestamp_basis varchar(20) NOT NULL DEFAULT 'SOURCE_REPORTED'
        CHECK (timestamp_basis IN ('SOURCE_REPORTED','LAPTOP_REPORTED')),
    ALTER COLUMN event_code DROP NOT NULL,
    ALTER COLUMN title DROP NOT NULL,
    ALTER COLUMN source_label DROP NOT NULL,
    ALTER COLUMN occurred_at DROP NOT NULL;

CREATE TABLE aislesignals_control.device_sync_receipts (
    device_id uuid NOT NULL,
    source_event_id uuid NOT NULL,
    organisation_id uuid NOT NULL,
    pharmacy_id uuid NOT NULL,
    receipt_id uuid NOT NULL UNIQUE,
    payload_hash char(64),
    expires_at timestamptz,
    withdrawn_at timestamptz,
    withdrawal_reason varchar(24) CHECK (withdrawal_reason IN ('LOCAL_DELETED','LOCAL_EXPIRED','LOCAL_EXPORT_REMOVED')),
    withdrawal_first boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (device_id,source_event_id),
    FOREIGN KEY (organisation_id,pharmacy_id,device_id)
        REFERENCES aislesignals_control.devices(organisation_id,pharmacy_id,id),
    CHECK ((withdrawn_at IS NULL) = (withdrawal_reason IS NULL))
);
CREATE INDEX device_sync_receipts_age ON aislesignals_control.device_sync_receipts(device_id,created_at);
CREATE INDEX device_sync_alert_cleanup ON aislesignals_control.alerts(device_id,source_expires_at)
    WHERE source_purged=false;
CREATE INDEX device_alert_intake_time ON aislesignals_control.alerts(device_id,received_at);
