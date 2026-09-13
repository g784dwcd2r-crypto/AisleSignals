CREATE SCHEMA IF NOT EXISTS aislesignals_control;
CREATE TABLE IF NOT EXISTS aislesignals_control.schema_version (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    version integer NOT NULL CHECK (version > 0),
    checksum char(64) NOT NULL,
    installed_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
