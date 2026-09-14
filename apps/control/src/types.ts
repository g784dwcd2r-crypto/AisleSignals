export type Role = "OWNER" | "MANAGER" | "REVIEWER";
export type Pharmacy = {
  id: string;
  organisation_id: string;
  name: string;
  address: string;
  timezone: string;
  active: boolean;
  version: number;
  created_at: string;
};
export type User = {
  id: string;
  name: string;
  email: string;
  role: Role;
  active: boolean;
  pharmacy_ids: string[];
  version: number;
  created_at: string;
};
export type Session = {
  user: Pick<User, "id" | "name" | "email" | "role">;
  organisation: { id: string; name: string };
  pharmacies: Pharmacy[];
  csrf_token: string;
};
export type Challenge = {
  challenge_token: string;
  expires_in_seconds: number;
  totp_secret?: string;
  totp_uri?: string;
};
export type Device = {
  id: string;
  pharmacy_id: string;
  pharmacy_name: string;
  name: string;
  platform: "MACOS" | "WINDOWS" | "OTHER";
  app_version: string;
  last_seen_at: string | null;
  connection_status: "ONLINE" | "OFFLINE" | "NEVER_CONNECTED" | "REVOKED";
  monitoring_status: "ACTIVE" | "STOPPED" | "DEGRADED" | "UNKNOWN";
  camera_count: number | null;
  version: number;
  revoked_at: string | null;
};
export type Outcome = "NORMAL_SHOPPING" | "UNCLEAR" | "SUSPECTED_INCIDENT";
export type AlertEvidenceState =
  "NONE" | "PENDING" | "PARTIAL" | "READY" | "EXPIRED";
export type AlertEvidence = {
  id: string;
  kind: "OVERVIEW" | "INTERACTION_CROP" | "CLIP";
  content_type: string;
  byte_count: number;
  state: "PENDING" | "READY" | "EXPIRED" | "DELETED" | "ERROR";
  expires_at: string;
};
export type Alert = {
  id: string;
  pharmacy_id: string;
  pharmacy_name: string;
  device_id: string;
  device_name: string;
  source_event_id: string;
  event_code: string;
  title: string;
  source_label: string;
  occurred_at: string;
  received_at: string;
  historical: boolean;
  timestamp_basis?: "SOURCE_REPORTED" | "LAPTOP_REPORTED";
  source_expires_at?: string | null;
  status: "OPEN" | "ACKNOWLEDGED" | "REVIEWED";
  version: number;
  review: null | { outcome: Outcome; note: string; by: string; at: string };
  incident_id: string | null;
  evidence: AlertEvidence[];
  evidence_state: AlertEvidenceState;
};
export type Incident = {
  id: string;
  pharmacy_id: string;
  pharmacy_name: string;
  alert_id: string;
  title: string;
  classification: Outcome;
  status: "OPEN" | "CLOSED";
  notes: string;
  reviewed_by: string;
  reviewed_at: string;
  created_at: string;
  version: number;
  source_unavailable?: boolean;
};
export type Dashboard = {
  generated_at: string;
  summary: {
    pharmacies: number;
    connected_laptops: number;
    total_laptops: number;
    open_alerts: number;
    reviewed_incidents: number;
  };
  devices: Device[];
  alerts: Alert[];
  incidents: Incident[];
};
export type Collection<T> = { items: T[] };
export type OneTimeToken = { token: string; expires_at: string };
export type Page =
  | "overview"
  | "pharmacies"
  | "laptops"
  | "downloads"
  | "alerts"
  | "incidents"
  | "users";
