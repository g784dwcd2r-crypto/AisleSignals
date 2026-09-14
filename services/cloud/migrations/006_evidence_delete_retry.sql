ALTER TABLE aislesignals_control.evidence_objects
    ADD COLUMN delete_attempts integer NOT NULL DEFAULT 0
        CHECK (delete_attempts BETWEEN 0 AND 1000000),
    ADD COLUMN delete_retry_at timestamptz,
    ADD COLUMN delete_error_at timestamptz;

CREATE INDEX evidence_objects_delete_retry
    ON aislesignals_control.evidence_objects (delete_retry_at, revoked_at, id)
    WHERE state = 'REVOKED';
